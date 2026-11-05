/* No external scripts, HTML injection, or execution of student code. */
const $ = id => document.getElementById(id);
const state = {id:null, data:null, ws:null, materials:[], selected:null, editTimer:null, lastText:'', localDirty:false, ended:false, reconnect:null};
let noticeTimer;
function notice(message){$('notice').textContent=message;$('notice').hidden=false;clearTimeout(noticeTimer);noticeTimer=setTimeout(()=>$('notice').hidden=true,6500);}
async function api(path, options={}){
  const response=await fetch(path, options);
  const data=await response.json();
  if(!response.ok) throw new Error(typeof data.detail==='string'?data.detail:'Request failed.');
  return data;
}
function send(event){
  if(state.ended){notice('This session is complete. Start a new session to continue.');return false;}
  if(state.ws?.readyState!==WebSocket.OPEN){notice('Connection interrupted. Please retry when connected.');return false;}
  state.ws.send(JSON.stringify(event));return true;
}
function flushEdits(){
  clearTimeout(state.editTimer);
  const text=$('workspace').value;
  if(text!==state.lastText && send({type:'student_action',action:'edit',content:text})){
    state.lastText=text;state.localDirty=false;
  }
}
function renderPlan(plan){
  $('objective').textContent=plan?.objective||`Practice ${state.data.current_concept}`;
  $('plan').replaceChildren();
  for(const step of plan?.steps||[]){const el=document.createElement('span');el.textContent=step;$('plan').append(el);}
}
function renderState(data){
  state.data=data;state.ended=data.ended;state.lastText=data.workspace||'';state.localDirty=false;
  $('workspace').value=state.lastText;$('problem').textContent=data.current_problem?.prompt||'Ask your tutor to get started.';
  $('concept').value=data.current_concept;$('proactive').checked=data.proactive_enabled;
  $('answer').value='';$('assessment').textContent='';$('highlight-preview').hidden=true;
  renderPlan(data.plan);$('end-session').disabled=state.ended;
}
function addStudent(text){const el=document.createElement('div');el.className='message student';el.textContent=text;$('chat-log').append(el);scrollChat();}
function scrollChat(){$('chat-log').scrollTop=$('chat-log').scrollHeight;}
function markText(element,text,start,end){
  // Server offsets count Unicode code points, while JS strings count UTF-16 units.
  const chars=Array.from(text), mark=document.createElement('mark');
  mark.textContent=chars.slice(start,end).join('');
  element.replaceChildren(document.createTextNode(chars.slice(0,start).join('')),mark,document.createTextNode(chars.slice(end).join('')));
}
function applyHighlight(metadata){
  if(metadata.target==='workspace'){
    const text=$('workspace').value;
    if(Array.from(text).slice(metadata.start,metadata.end).join('')!==metadata.quote){notice('The highlighted passage has changed. Ask for a fresh highlight.');return;}
    $('highlight-preview').hidden=false;markText($('highlight-preview'),text,metadata.start,metadata.end);
    const chars=Array.from(text);$('workspace').focus();
    $('workspace').setSelectionRange(chars.slice(0,metadata.start).join('').length,chars.slice(0,metadata.end).join('').length);
  }else{
    const material=state.materials.find(m=>m.chunks.some(c=>c.id===metadata.source_id));
    const chunk=material?.chunks.find(c=>c.id===metadata.source_id);
    if(chunk){selectMaterial(material.id);markText($('material-view'),chunk.text,metadata.start,metadata.end);}
  }
}
function renderDiagram(metadata){
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg');
  const nodes=metadata.nodes||[],cols=2,rows=Math.ceil(nodes.length/cols);
  svg.setAttribute('viewBox',`0 0 320 ${rows*85+15}`);svg.classList.add('diagram');svg.setAttribute('role','img');svg.setAttribute('aria-label','Concept graph');
  const positions=new Map(nodes.map((n,i)=>[n.id,{x:10+(i%cols)*155,y:15+Math.floor(i/cols)*85}]));
  for(const edge of metadata.edges||[]){
    const a=positions.get(edge.source),b=positions.get(edge.target);if(!a||!b)continue;
    const line=document.createElementNS(ns,'path');
    line.setAttribute('d',`M ${a.x+70} ${a.y+27} L ${b.x+70} ${b.y+27}`);line.setAttribute('stroke','#9eb99e');line.setAttribute('fill','none');svg.append(line);
    const tip=document.createElementNS(ns,'circle');tip.setAttribute('cx',(a.x+b.x)/2+70);tip.setAttribute('cy',(a.y+b.y)/2+27);tip.setAttribute('r','3');tip.setAttribute('fill','#608361');svg.append(tip);
  }
  for(const node of nodes){
    const {x,y}=positions.get(node.id),rect=document.createElementNS(ns,'rect');
    Object.entries({x,y,width:140,height:54,rx:7,fill:'#e8f0e8',stroke:'#cbdac7'}).forEach(([k,v])=>rect.setAttribute(k,v));svg.append(rect);
    const label=document.createElementNS(ns,'text');label.setAttribute('x',x+70);label.setAttribute('y',y+30);label.setAttribute('text-anchor','middle');label.setAttribute('font-size','10');label.setAttribute('fill','#275f49');label.textContent=node.label.length>24?node.label.slice(0,22)+'…':node.label;
    const title=document.createElementNS(ns,'title');title.textContent=node.label;rect.append(title);svg.append(label);
  }
  return svg;
}
function addIntervention(message, restoring=false){
  if(!restoring && message.proactive && state.localDirty)return;
  const result=message.result,el=document.createElement('div');el.className='message tutor';
  const label=document.createElement('div');label.className='label';label.textContent=(message.proactive?'A TIMELY NUDGE · ':'')+result.type.replaceAll('_',' ').toUpperCase();
  const text=document.createElement('div');text.className='content';text.textContent=result.content;el.append(label,text);
  if(result.type==='highlight'){
    const btn=document.createElement('button');btn.className='quiet';btn.textContent='Show highlighted passage';btn.onclick=()=>applyHighlight(result.metadata);el.append(btn);
  }
  if(result.type==='visualization'){
    el.append(renderDiagram(result.metadata));const ol=document.createElement('ol');ol.className='trace';
    for(const step of result.metadata.steps||[]){const li=document.createElement('li');li.textContent=step;ol.append(li);}el.append(ol);
    const references=document.createElement('div');references.className='source-ref';references.textContent=(result.metadata.edges||[]).map(e=>`${e.source} → ${e.target}`).join(' · ');el.append(references);
  }
  if(result.metadata.retrieved_sources?.length){const sources=document.createElement('div');sources.className='source-ref';sources.textContent='Retrieved context: '+[...new Set(result.metadata.retrieved_sources.map(s=>s.source))].join(', ');el.append(sources);}
  if(message.proactive && !restoring){
    const feedback=document.createElement('div');feedback.className='feedback';
    for(const [rating,label] of [[1,'Helpful'],[-1,'Not helpful']]){const button=document.createElement('button');button.textContent=label;button.onclick=()=>{if(send({type:'feedback',rating,intervention_id:message.event_id})){feedback.replaceChildren(document.createTextNode('Feedback recorded.'));}};feedback.append(button);}el.append(feedback);
  }
  $('chat-log').append(el);if(message.plan)renderPlan(message.plan);scrollChat();
}
function showAssessment(result){$('assessment').textContent=`${result.verified?(result.correct?'✓ Correct.':'Try again. '):'Provisional review: '}${result.reason}`;}
function onMessage(event){
  const m=JSON.parse(event.data);
  if(m.type==='intervention')addIntervention(m);
  if(m.type==='assessment')showAssessment(m.result);
  if(m.type==='plan')renderPlan(m.plan);
  if(m.type==='state')renderState(m.state);
  if(m.type==='busy')$('busy').hidden=!m.active;
  if(m.type==='error')notice(m.message);
  if(m.type==='session_summary'){state.ended=true;addIntervention({result:{type:'explanation',content:m.result.summary+'\n\nNext goal: '+m.result.next_goal,metadata:{}}});notice('Session complete. Your learning summary has been saved.');}
}
function connect(){
  const socket=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/${state.id}`);state.ws=socket;
  socket.onopen=()=>{$('status').textContent=state.ended?'Session complete':'Connected';$('dot').style.background='#5d936c';};
  socket.onmessage=onMessage;
  socket.onclose=()=>{if(state.ws!==socket)return;$('status').textContent='Reconnecting';$('dot').style.background='#c8a068';state.reconnect=setTimeout(connect,2000);};
}
async function startSession(fresh=false){
  clearTimeout(state.reconnect);clearTimeout(state.editTimer);
  if(state.ws){state.ws.onclose=null;state.ws.close();}
  let session;
  const stored=localStorage.getItem('studyroom-session');
  if(!fresh && stored){try{session=await api(`/api/sessions/${Number(stored)}`);}catch{localStorage.removeItem('studyroom-session');}}
  if(!session)session=await api('/api/sessions',{method:'POST'});
  state.id=session.id;localStorage.setItem('studyroom-session',String(state.id));renderState(session.state);
  $('chat-log').replaceChildren();
  if(session.history?.length){for(const e of session.history){if(e.type==='student_message')addStudent(e.data.content);if(e.type==='intervention')addIntervention({result:e.data,proactive:e.data.proactive,event_id:e.id},true);if(e.type==='assessment')showAssessment(e.data);}}
  else{const greeting=document.createElement('div');greeting.className='welcome';const title=document.createElement('h3');title.textContent='You do the thinking. I’ll help you along.';const text=document.createElement('p');text.textContent='Try the problem, ask a question, or choose a teaching tool below.';greeting.append(title,text);$('chat-log').append(greeting);}
  connect();
}
function selectMaterial(id){state.selected=id;const record=state.materials.find(m=>m.id===id);$('material-view').textContent=record?.chunks.map(c=>c.text).join('\n\n')||'';document.querySelectorAll('#material-list button').forEach(b=>b.classList.toggle('selected',b.dataset.id===id));}
async function loadMaterials(){state.materials=await api('/api/materials');$('material-count').textContent=state.materials.length;$('material-list').replaceChildren();for(const m of state.materials){const b=document.createElement('button');b.dataset.id=m.id;b.textContent='▤  '+m.name;b.onclick=()=>selectMaterial(m.id);$('material-list').append(b);}selectMaterial(state.selected||state.materials[0]?.id);}
function ask(tool=null,content=null){flushEdits();const text=content||$('message').value.trim()||(tool?`Please use ${tool.replaceAll('_',' ')} to help with my current work.`:'');if(!text)return;if(send({type:'student_message',content:text,tool})){addStudent(text);$('message').value='';}}
$('chat-form').onsubmit=e=>{e.preventDefault();ask();};
$('message').onkeydown=e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();ask();}};
document.querySelectorAll('[data-tool]').forEach(button=>button.onclick=()=>ask(button.dataset.tool));
$('request-hint').onclick=()=>ask('hint');
$('check-work').onclick=()=>{flushEdits();send({type:'problem_attempt',content:$('workspace').value,problem_id:state.data.current_problem.id,assessment_mode:'reasoning'});};
$('report-test').onclick=()=>{const report=window.prompt('Paste results from tests you ran in your own environment. These are recorded as unverified reports.');if(report?.trim()){flushEdits();send({type:'code_execution',content:report});notice('Test report recorded.');}};
$('workspace').oninput=()=>{state.localDirty=true;$('highlight-preview').hidden=true;clearTimeout(state.editTimer);state.editTimer=setTimeout(flushEdits,1000);};
$('workspace').onkeydown=e=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z'){setTimeout(()=>{flushEdits();send({type:'student_action',action:'revert',content:$('workspace').value});},0);}};
$('attempt-form').onsubmit=e=>{e.preventDefault();flushEdits();send({type:'problem_attempt',content:$('answer').value,problem_id:state.data.current_problem.id});};
$('proactive').onchange=()=>send({type:'preferences',proactive_enabled:$('proactive').checked});
$('new-session').onclick=()=>startSession(true).catch(e=>notice(e.message));
$('end-session').onclick=()=>{flushEdits();if(send({type:'session_end'})){$('end-session').disabled=true;}};
$('generate-problem').onclick=async()=>{flushEdits();$('generate-problem').disabled=true;try{await api(`/api/sessions/${state.id}/problem`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({concept:$('concept').value})});}catch(e){notice(e.message);}finally{$('generate-problem').disabled=false;}};
$('upload').onchange=async()=>{const file=$('upload').files[0];if(!file)return;const data=new FormData();data.append('file',file);notice('Reading your material and extracting concepts…');try{await api('/api/materials',{method:'POST',body:data});await loadMaterials();notice('Material ready. Your tutor can now retrieve relevant passages.');}catch(e){notice(e.message);}finally{$('upload').value='';}};
setInterval(()=>{if(!document.hidden && !state.ended && state.ws?.readyState===WebSocket.OPEN)send({type:'student_action',action:'idle'});},20000);
async function init(){const health=await api('/api/health');if(health.demo_mode){$('tutor-mode').textContent='Offline demo · scripted teaching examples';$('tutor-mode').classList.add('notice-demo');}else if(!health.configured)notice('Add an OpenAI API key to enable tutoring, or run with DEMO_MODE=true.');await loadMaterials();await startSession();}
init().catch(e=>notice(e.message));
