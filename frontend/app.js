const $ = id => document.getElementById(id);
const state = {id:null, ws:null, study:null, lastText:'', dirty:false, pending:false,
  uploading:false, timer:null, reconnect:null, generation:0, currentProblem:null};

function notice(message){$('notice').textContent=message;$('notice').hidden=!message;}
async function api(path,options={}){
  const response=await fetch(path,options);
  const text=await response.text();let data;
  try{data=JSON.parse(text);}catch{throw Error(`Server request failed (${response.status}). Check the backend log.`);}
  if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:`Request failed (${response.status}).`);
  return data;
}
function send(event){
  if(state.ws?.readyState!==WebSocket.OPEN){
    notice('Connection interrupted. Reconnecting; your work remains here.');return false;
  }
  state.ws.send(JSON.stringify(event));
  return true;
}
function flushEdits(){
  clearTimeout(state.timer);const text=$('workspace').value;
  if(text!==state.lastText){
    if(!send({type:'student_action',action:'edit',content:text}))return false;
    state.lastText=text;state.dirty=false;
  }
  return true;
}
function renderObserver(o={}){
  $('observer-status').textContent=`Observer: ${o.decision||'Not evaluated yet'} — ${o.reason||'Waiting for activity'}`;
  $('observer-details').textContent=JSON.stringify(o,null,2);
}
function updateControls(){
  const problem=state.study?.state.current_problem;
  const disabled=state.uploading||state.pending||!problem?.id||state.study?.state.study_complete||state.study?.state.ended;
  document.querySelectorAll('[data-tool]').forEach(b=>b.disabled=!!disabled||state.toolPending||state.ws?.readyState!==WebSocket.OPEN);
  $('skip').disabled=!!disabled||state.toolPending||state.ws?.readyState!==WebSocket.OPEN;
  $('answer').disabled=!!disabled;$('submit').disabled=!!disabled;
  $('workspace').disabled=state.uploading||!problem?.id||!!state.study?.state.study_complete||!!state.study?.state.ended;
}
function svgElement(tag,attrs={}){
  const el=document.createElementNS('http://www.w3.org/2000/svg',tag);
  for(const [key,value] of Object.entries(attrs))el.setAttribute(key,String(value));
  return el;
}
function diagram(graph,showMastery=false){
  const nodes=graph.nodes||[],edges=graph.edges||[],depth=new Map(nodes.map(n=>[n.id,0]));
  for(let i=0;i<nodes.length;i++)for(const e of edges){
    if(depth.has(e.source)&&depth.has(e.target))depth.set(e.target,Math.min(nodes.length,Math.max(depth.get(e.target),depth.get(e.source)+1)));
  }
  const positions=new Map();let row=0;
  const levels=[...new Set(depth.values())].sort((a,b)=>a-b);
  for(const level of levels){const group=nodes.filter(n=>depth.get(n.id)===level);
    group.forEach((n,i)=>positions.set(n.id,{x:12+(i%2)*306,y:12+(row+Math.floor(i/2))*102}));
    row+=Math.ceil(group.length/2);
  }
  const svg=svgElement('svg',{viewBox:`0 0 620 ${Math.max(120,row*102+10)}`,role:'img','aria-label':showMastery?'Concept graph and estimated mastery':'Teaching visualization'});
  const markerId='arrow-'+Math.random().toString(36).slice(2);
  const defs=svgElement('defs'),marker=svgElement('marker',{id:markerId,viewBox:'0 0 10 10',refX:9,refY:5,markerWidth:6,markerHeight:6,orient:'auto-start-reverse'});
  marker.append(svgElement('path',{d:'M 0 0 L 10 5 L 0 10 Z',fill:'#9aaf92'}));defs.append(marker);svg.append(defs);
  for(const edge of edges){const a=positions.get(edge.source),b=positions.get(edge.target);if(!a||!b)continue;
    svg.append(svgElement('path',{d:`M ${a.x+140} ${a.y+66} C ${a.x+140} ${a.y+90}, ${b.x+140} ${b.y-22}, ${b.x+140} ${b.y}`,fill:'none',stroke:'#9aaf92','stroke-width':2,'marker-end':`url(#${markerId})`}));
  }
  for(const node of nodes){const p=positions.get(node.id),value=node.mastery;
    const fill=node.active?'#fff1d9':(value!=null&&value>=(state.study?.mastery_target??.75))?'#e1efdf':'#f1f4ed';
    const group=svgElement('g');group.append(svgElement('rect',{x:p.x,y:p.y,width:280,height:66,rx:8,fill,stroke:node.active?'#bd842c':'#cfdbc8','stroke-width':node.active?2:1}));
    const full=node.label||node.id,title=svgElement('title');title.textContent=full;group.append(title);
    const label=svgElement('text',{x:p.x+14,y:p.y+25,'font-size':13,fill:'#263d2e'});label.textContent=full.length>33?full.slice(0,31)+'…':full;group.append(label);
    if(showMastery){const score=svgElement('text',{x:p.x+14,y:p.y+48,'font-size':11,fill:'#667b5c'});score.textContent=value==null?'Not assessed yet':`Estimated mastery: ${Math.round(value*100)}%`;group.append(score);}
    svg.append(group);
  }
  return svg;
}
function renderStudy(study){
  const changed=state.currentProblem!==study.state.current_problem?.id;
  state.study=study;renderObserver(study.state.observer_status);
  $('concept').textContent=study.state.study_complete?'Material complete':study.state.current_concept;
  const node=study.graph.nodes.find(n=>n.id===study.state.current_concept),value=node?.mastery;
  $('mastery').textContent=value==null?'Estimated mastery: Not assessed yet':`Estimated mastery: ${Math.round(value*100)}%`;
  $('mastery-bar').hidden=value==null;$('mastery-bar').value=value??0;
  $('problem').textContent=study.state.study_complete?'Every concept has reached the current progression target. You can review your learning map or upload another material.':study.state.current_problem.prompt;
  $('graph').replaceChildren(diagram(study.graph,true));
  $('material-name').textContent=study.material.name;
  if(changed){
    $('assessment').textContent=state.currentProblem?'Previous answer was correct. Continue with the new question.':'';
    clearTimeout(state.timer);state.currentProblem=study.state.current_problem.id;
    $('workspace').value=study.state.workspace||'';state.lastText=$('workspace').value;state.dirty=false;
    $('answer').value='';$('interventions').replaceChildren();$('highlight-preview').hidden=true;
    state.pending=false;
  }
  if(study.state.current_problem?.solved)state.pending=true;
  $('status').textContent=study.state.study_complete?'Learning target reached':study.material.name;
  if(!$('source-details').open)$('material-view').textContent=study.material.chunks[0]?.text||'';
  updateControls();
}
function markText(element,text,start,end){
  const chars=Array.from(text),mark=document.createElement('mark');mark.textContent=chars.slice(start,end).join('');
  element.replaceChildren(document.createTextNode(chars.slice(0,start).join('')),mark,document.createTextNode(chars.slice(end).join('')));
}
function showHighlight(m){
  if(Array.isArray(m.issues)){
    const lines=$('workspace').value.split(/\r?\n/);
    if(m.issues.some(i=>lines[i.line-1]!==i.quote))return;
    const preview=$('highlight-preview');preview.hidden=false;preview.replaceChildren();
    lines.forEach((line,index)=>{const issue=m.issues.find(i=>i.line===index+1);const el=document.createElement(issue?'mark':'span');el.textContent=line+(index<lines.length-1?'\n':'');if(issue){el.className='reasoning-issue';el.title=issue.reason;}preview.append(el);});
    preview.append(document.createTextNode('\n'));
    syncReasoningHighlight();
    return;
  }
  if(m.target==='workspace'){
    const text=$('workspace').value;if(Array.from(text).slice(m.start,m.end).join('')!==m.quote)return;
    $('highlight-preview').hidden=false;markText($('highlight-preview'),text,m.start,m.end);$('highlight-preview').append(document.createTextNode('\n'));syncReasoningHighlight();
  }else{
    const chunk=state.study.material.chunks.find(c=>c.id===m.source_id);if(!chunk)return;
    $('source-details').open=true;markText($('material-view'),chunk.text,m.start,m.end);
  }
}
function intervention(message){
  if(message.proactive&&state.dirty)return;
  const result=message.result,m=result.metadata||{},card=document.createElement('div');card.className='intervention';
  const label=document.createElement('strong');label.textContent=result.type.replaceAll('_',' ');
  const text=document.createElement('p');text.textContent=result.content;card.append(label,text);
  if(result.type==='highlight')showHighlight(m);
  if(result.type==='visualization'){
    card.append(diagram(m));const list=document.createElement('ol');
    for(const step of m.steps||[]){const li=document.createElement('li');li.textContent=step;list.append(li);}card.append(list);
  }
  $('interventions').append(card);
}
function handleMessage(event,generation){
  if(generation!==state.generation)return;
  const message=JSON.parse(event.data);
  if(message.type==='observer_status')renderObserver(message);
  if(message.type==='busy'&&!message.active){state.toolPending=false;updateControls();}
  if(message.type==='error'){state.toolPending=false;}
  if(message.type==='study_state')renderStudy(message);
  if(message.type==='intervention')intervention(message);
  if(message.type==='assessment'){
    const r=message.result;$('assessment').textContent=`${r.correct?'Correct. Preparing your next question.':'Keep trying.'} ${r.reason}`;
    state.pending=r.correct&&r.verified;updateControls();
  }
  if(message.type==='busy')$('busy').hidden=!message.active;
  if(message.type==='error'){notice(message.message);state.pending=false;updateControls();}
}
function connect(){
  clearTimeout(state.reconnect);const generation=state.generation;
  const ws=new WebSocket(`${location.protocol==='https:'?'wss':'ws'}://${location.host}/ws/${state.id}`);state.ws=ws;
  ws.onmessage=e=>handleMessage(e,generation);
  ws.onopen=async()=>{if(generation!==state.generation)return;try{
    const view=await resumeStudy();if(generation!==state.generation)return;
    renderStudy(view);flushEdits();
  }catch(e){notice(e.message);}};
  ws.onclose=()=>{if(generation!==state.generation||state.uploading)return;updateControls();$('observer-status').textContent='Observer: Disconnected';$('status').textContent='Reconnecting…';state.reconnect=setTimeout(connect,2000);};
}
$('workspace').oninput=()=>{state.dirty=true;$('highlight-preview').hidden=true;clearTimeout(state.timer);state.timer=setTimeout(flushEdits,1000);};
$('answer-form').onsubmit=e=>{
  e.preventDefault();if(state.pending||!$('answer').value.trim()||!flushEdits())return;
  if(send({type:'problem_attempt',content:$('answer').value,problem_id:state.currentProblem})){
    notice('');state.pending=true;updateControls();
  }
};
$('upload').onchange=async()=>{
  const file=$('upload').files[0];if(!file||state.uploading)return;
  state.uploading=true;state.generation++;clearTimeout(state.timer);clearTimeout(state.reconnect);
  if(state.ws){state.ws.onclose=null;state.ws.close();state.ws=null;}
  updateControls();notice('');$('status').textContent='Extracting concepts and building your learning map…';
  try{
    const form=new FormData();form.append('file',file);
    const material=await api('/api/materials',{method:'POST',body:form});
    $('status').textContent='Choosing your first question…';
    const session=await api('/api/sessions',{method:'POST'});state.id=session.id;
    const study=await api(`/api/sessions/${state.id}/study`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({material_id:material.id})});
    state.pending=false;state.currentProblem=null;renderStudy(study);
    localStorage.setItem('studyroom-auto-session',String(state.id));connect();
  }catch(e){notice(e.message);$('status').textContent='Upload could not be completed';}
  finally{state.uploading=false;$('upload').value='';updateControls();}
};
setInterval(()=>{
  if(!document.hidden&&!state.uploading&&!state.study?.state.study_complete&&state.currentProblem&&state.ws?.readyState===WebSocket.OPEN)send({type:'student_action',action:'idle'});
},20000);
async function resumeStudy(){
  const view=await api(`/api/sessions/${state.id}/study`);
  if(view.state.current_problem?.solved&&!view.state.study_complete){
    return api(`/api/sessions/${state.id}/study`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({material_id:view.state.material_id})});
  }
  return view;
}
async function init(){
  const saved=localStorage.getItem('studyroom-auto-session');if(!saved)return;
  state.id=Number(saved);
  const generation=state.generation;
  try{const study=await resumeStudy();if(generation!==state.generation)return;renderStudy(study);connect();}
  catch(e){notice(e.message);localStorage.removeItem('studyroom-auto-session');}
}

// CODE STARTS
init();

for(const tool of ['hint','explanation','highlight','visualization','guiding_question']){
 const b=document.createElement('button');
 b.type='button';
 b.dataset.tool=tool;
 b.textContent=tool.replaceAll('_',' ');
 b.disabled=true;
 b.onclick=()=>{if(b.disabled||!flushEdits())
                  return;
                if(send({type:'student_action',action:'hint_request',tool})){
                  state.toolPending=true;
                  $('busy').textContent='Generating your requested '+b.textContent+'…';
                  $('busy').hidden=false;
                  updateControls();
                }
                };
 $('manual-tools').append(b);
}

$('skip').onclick=async()=>{
 if($('skip').disabled||!flushEdits())return;
 state.pending=true;updateControls();
 try{const study=await api(`/api/sessions/${state.id}/skip`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({problem_id:state.currentProblem})});renderStudy(study);$('assessment').textContent='Question skipped. Mastery unchanged.';}
 catch(e){notice(e.message);}finally{state.pending=false;updateControls();}
};

function syncReasoningHighlight(){
 const input=$('workspace'),preview=$('highlight-preview');
 preview.style.width=input.clientWidth+'px';
 preview.style.height=input.clientHeight+'px';
 preview.scrollTop=input.scrollTop;preview.scrollLeft=input.scrollLeft;
}
$('workspace').addEventListener('scroll',syncReasoningHighlight);
if(typeof ResizeObserver!=='undefined')new ResizeObserver(syncReasoningHighlight).observe($('workspace'));
