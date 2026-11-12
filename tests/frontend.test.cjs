const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { SourceTextModule } = require('node:vm');
const { JSDOM } = require('jsdom');

const build = path.join(__dirname, '..', 'frontend', 'dist');
const html = fs.readFileSync(path.join(build, 'index.html'), 'utf8');
const script = fs.readFileSync(path.join(build, html.match(/src="\/(assets\/[^"]+\.js)"/)[1]), 'utf8');
const workspace = '😀 left = mid + 1';
const study = {
  type: 'study_state',
  state: { material_id: 'm1', current_concept: 'Binary Search', workspace,
    plan: { objective: 'Understand the topic', steps: ['Explain the invariant'], learning_goals: ['Solve independently'] }, plan_version: 1,
    observer_status: { revision: 1 }, current_problem: { id: 'p1', prompt: 'Find the first 2.' } },
  material: { id: 'm1', name: 'course.md', chunks: [] },
  graph: { nodes: [], edges: [] }, mastery_target: 0.75,
};

async function until(predicate) {
  for (let attempt = 0; attempt < 100; attempt++) {
    const result = predicate();
    if (result) return result;
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  throw Error('Expected UI update did not occur');
}

async function setup(t, { draft, authenticated = true, openSession = true, failedUpload = false, workflowData } = {}) {
  const dom = new JSDOM(html, { url: 'http://localhost:8000', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window;
  const messages = [];
  const traces = workflowData ?? {runs:[], details:{}, events:[], artifacts:{}};
  const workflowReads = [];
  const requests = [];
  let socket;
  let operationReady=false;
  let memorySnapshot={revision:1,short_term:{concept:'Binary Search',question:'Find the first 2.',response:{text:workspace},plan:study.state.plan,assessment_pending:false},long_term:{mastery:{'Binary Search':.5},hypotheses:[]},items:[{id:'summary1',kind:'summary',original_text:'Practiced search.',effective_text:'Practiced search.',disputed:false,student_note:'',edits:[]}]};
  const intervals=[];
  const setInterval=w.setInterval.bind(w);
  w.setInterval=(callback,delay,...args)=>{intervals.push({callback,delay});return setInterval(callback,delay,...args);};
  w.localStorage.setItem('studyroom-auto-session-1', '1');
  if (draft) w.localStorage.setItem('studyroom-draft-1-1', JSON.stringify({questionId:'p1',response:draft}));
  w.DOMMatrixReadOnly = class { m22 = 1; };
  w.fetch = async (url, options) => {
    if(url.startsWith('/api/workflow/artifacts/')) {workflowReads.push(url);return {ok:true,json:async()=>({data:traces.artifacts[url.split('/').at(-1)] ?? {}})};}
    if(url.startsWith('/api/workflow/runs/')) {const id=url.split('/').at(-1);return {ok:true,json:async()=>id==='prep' ? {id:'prep',name:'Topic/upload preparation',status:'completed',started_at:'2026-11-12T12:00:00Z',trigger:{},spans:[],edges:[],artifacts:[],events:[{id:'p1',run_id:'prep',cursor:1,type:'progress.selected',category:'tools',status:'completed',timestamp:'2026-11-12T12:00:00Z',data:{title:'RAG guide',url:'https://example.org/rag',reason:'Explains retrieval and grounding.'}}]} : structuredClone(traces.details[id])};}
    if(url.includes('/workflow/events')) {const after=Number(new URL(url,'http://localhost').searchParams.get('after')??0);const events=traces.events.filter(e=>e.cursor>after);return {ok:true,json:async()=>({events,cursor:events.at(-1)?.cursor??after,has_more:false,watermark:events.at(-1)?.cursor??after})};}
    if(url.includes('/workflow')) {
      const all=url.startsWith('/api/operations/j1/') ? [{id:'prep',name:'Topic/upload preparation',status:'completed',cursor:1}] : url.startsWith('/api/sessions/1/') ? traces.runs : [];
      const before=Number(new URL(url,'http://localhost').searchParams.get('before')??Infinity);
      const runs=all.filter(r=>!['queued','running','waiting'].includes(r.status) && r.cursor<before);
      return {ok:true,json:async()=>({runs:structuredClone(runs.slice(0,2)),active_runs:structuredClone(all.filter(r=>['queued','running','waiting'].includes(r.status))),next_cursor:runs.length>2 ? runs[1].cursor : undefined,watermark:traces.events.at(-1)?.cursor??0,tracking:'complete'})};
    }

    if (url === '/api/auth/me') return { ok: authenticated, status: authenticated ? 200 : 401,
      json: async () => authenticated ? {user: {id: 1, email: 'student@example.com'}} : {detail: 'Please log in.'} };
    if (url === '/api/sessions' && options?.method==='POST') return {ok:true,json:async()=>({id:3})};
    if (url === '/api/sessions') return { ok: true, json: async () => ({sessions: [
      {id: 1, material_id: 'm1', title: 'course.md', created_at: '2026-11-09T10:00:00Z', ended: false},
      {id: 2, material_id: 'm2', title: 'other-course.md', created_at: '2026-11-08T10:00:00Z', ended: false},
    ]}) };
    if (url === '/api/sessions/2/study') return {ok: true, json: async () => ({
      ...study, state: {...study.state, workspace: 'Second chat reasoning'},
      material: {...study.material, id: 'm2', name: 'other-course.md'},
    })};
    if (url === '/api/sessions/2') return {ok: true, json: async () => ({history: [
      {id: 60, type: 'student_message', data: {content: 'Question from the second chat'}},
    ]})};
    if(url.endsWith('/memory')) return {ok:true,json:async()=>memorySnapshot};
    if(url.endsWith('/memory/corrections')) {const body=JSON.parse(options.body);requests.push({path:url,...body});memorySnapshot={...memorySnapshot,revision:2,items:memorySnapshot.items.map(item=>({...item,effective_text:body.text}))};return {ok:true,json:async()=>memorySnapshot};}
    if(url==='/api/material-jobs') return {ok:true,json:async()=>({id:'j1'})};
    if(url==='/api/material-jobs/topic'){const body=JSON.parse(options.body);requests.push({path:url,...body});return {ok:true,json:async()=>({id:'j1'})};}
    if(url==='/api/material-jobs/j1/meaning'){requests.push({path:url,...JSON.parse(options.body)});operationReady=true;return {ok:true,json:async()=>({id:'j1'})};}
    if(url==='/api/operations/j1') return {ok:true,json:async()=>({id:'j1',kind:failedUpload?'upload':'topic',error:failedUpload?'The provider rejected graph preparation.':undefined,status:failedUpload?'failed':operationReady?'ready':'needs_clarification',result:operationReady?{id:'m1'}:{type:'choices',meanings:[{label:'Retrieval-augmented generation',description:'Grounding answers in documents.',query:'RAG AI'}]},events:[{id:1,kind:'selected',title:'RAG guide',url:'https://example.org/rag',reason:'Explains retrieval and grounding.'}]})};
    if(options?.method === 'PUT'){ const body=JSON.parse(options.body); requests.push(body); return {ok:true,json:async()=>({type:'plan',plan:body.plan,plan_version:body.expected_version+1})}; }
    if(url==='/api/materials/web'){const body=JSON.parse(options.body);requests.push({path:url,...body});return body.meaning
      ?{ok:false,status:400,json:async()=>({detail:'stop after choice'})}
      :{ok:true,json:async()=>({type:'choices',topic:body.topic,meanings:[
        {label:'Retrieval-augmented generation',query:'retrieval-augmented generation LLM',description:'Grounding answers in documents.'},
        {label:'Rag (textile)',query:'rag cloth',description:'A scrap of cloth.'}]})};}
    if(url.endsWith('/concept')){const body=JSON.parse(options.body);requests.push({path:url,...body});return {ok:true,json:async()=>({...study,state:{...study.state,current_concept:body.concept,current_problem:{id:'p3',prompt:'Question about '+body.concept}}})};}
    if(url.endsWith('/web-enhance')){const body=JSON.parse(options.body);requests.push({path:url,...body});return {ok:true,json:async()=>({id:'m1',web_enhance:body.enabled})};}
    if(url.endsWith('/next')){requests.push({path:url,...JSON.parse(options.body)});return {ok:true,json:async()=>({...study,state:{...study.state,current_problem:{id:'p2',prompt:'Next question'}}})};}
    return { ok: true, json: async () => url.endsWith('/study') ? study : { history: [] } };
  };
  w.matchMedia = () => ({ matches: false, addListener() {}, removeListener() {}, addEventListener() {}, removeEventListener() {} });
  w.ResizeObserver = class { observe() {} unobserve() {} disconnect() {} };
  w.Range.prototype.getClientRects = () => [];
  w.Range.prototype.getBoundingClientRect = () => new w.DOMRect();
  w.WebSocket = class {
    static OPEN = 1;
    readyState = 1;
    constructor() { socket = this; queueMicrotask(() => this.onopen()); }
    send(raw) { messages.push(JSON.parse(raw)); }
    close() {}
  };
  const entry = new SourceTextModule(script, { context: dom.getInternalVMContext() });
  await entry.link(() => { throw Error('Unexpected static import in the built entry'); });
  await entry.evaluate();
  const document = w.document;
  await until(() => document.querySelector(authenticated ? '.new-chat-content' : '#auth-email'));
  if (authenticated && openSession) {
    const saved = await until(() => document.querySelector('.session-item'));
    saved.click();
    await until(() => document.querySelector('#submit:not(:disabled)'));
  }
  return { document, messages, requests, intervals, workflowReads, traces, disconnect:()=>socket.onclose({code:1006}), reopen:()=>socket.onopen(), window: w, dispatch: data => socket.onmessage({ data: JSON.stringify(data) }),
    captureDispatch: () => { const saved = socket; return data => saved.onmessage({data: JSON.stringify(data)}); } };
}

test('login starts a new chat and saved sessions can be opened and left', async t => {
  const { document, dispatch, messages } = await setup(t, {openSession: false});
  assert.equal(document.querySelector('#workspace'), null);
  assert.equal(messages.length, 0);
  const saved = await until(() => document.querySelector('.session-item'));
  assert.match(saved.textContent, /course.md/);
  saved.click();
  await until(() => document.querySelector('#submit:not(:disabled)'));
  dispatch({type: 'intervention', event_id: 50, revision: 1,
    result: {type: 'hint', content: 'Help in the earlier chat.', metadata: {}}});
  await until(() => document.querySelector('.intervention'));
  document.querySelector('.new-chat-button').click();
  await until(() => document.querySelector('.new-chat-content'));
  assert.equal(document.querySelector('.intervention'), null);
  assert.equal(document.querySelector('#workspace'), null);
});

test('theme, study tabs and mobile navigation preserve the current response', async t => {
  const {document, window} = await setup(t);
  const response = (await until(() => document.querySelector('.cm-line'))).textContent;
  assert.equal(document.documentElement.dataset.theme, 'dark');
  document.querySelector('[aria-label="Switch to light mode"]').click();
  await until(() => document.documentElement.dataset.theme === 'light');
  assert.equal(window.localStorage.getItem('studyroom-theme'), 'light');
  assert.equal(document.querySelector('.cm-line').textContent, response);
  document.querySelector('#map-tab').click();
  await until(() => document.querySelector('#workspace-panel').hidden);
  assert.equal(document.querySelector('#map-panel').hidden, false);
  document.querySelector('#map-tab').dispatchEvent(new window.KeyboardEvent('keydown', {key: 'ArrowLeft', bubbles: true}));
  await until(() => !document.querySelector('#workspace-panel').hidden);
  document.querySelector('[aria-label="Open chat sessions"]').click();
  await until(() => document.querySelector('.chat-app').classList.contains('menu-open'));
  document.querySelector('.sidebar-backdrop').click();
  await until(() => !document.querySelector('.chat-app').classList.contains('menu-open'));
});

test('switching chats restores their own conversation and ignores the previous socket', async t => {
  const {document, captureDispatch} = await setup(t);
  const previousSocket = captureDispatch();
  [...document.querySelectorAll('.session-item')].find(button => button.textContent.includes('other-course.md')).click();
  await until(() => document.querySelector('.cm-line')?.textContent === 'Second chat reasoning');
  assert.match(document.querySelector('#interventions').textContent, /Question from the second chat/);
  previousSocket({type: 'intervention', event_id: 99, revision: 1,
    result: {type: 'hint', content: 'Stale first chat reply', metadata: {}}});
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.doesNotMatch(document.querySelector('#interventions').textContent, /Stale first chat reply/);
});

test('signed-out visitors see login and logout removes the study workspace', async t => {
  const guest = await setup(t, {authenticated: false});
  assert.equal(guest.document.querySelector('#upload'), null);
  assert.equal(guest.messages.length, 0);
  const signup = [...guest.document.querySelectorAll('button')].find(button => button.textContent === 'Sign up');
  signup.click();
  await until(() => guest.document.querySelector('h1').textContent === 'Create your account');
  const signedIn = await setup(t);
  const logout = [...signedIn.document.querySelectorAll('button')].find(button => button.textContent === 'Log out');
  logout.click();
  await until(() => signedIn.document.querySelector('#auth-email'));
  assert.equal(signedIn.document.querySelector('#workspace'), null);
});

test('annotations decorate exact Unicode-aware ranges and dismissal preserves student text', async t => {
  const { document, dispatch } = await setup(t);
  dispatch({ type: 'intervention', event_id: 1, revision: 1, result: {
    type: 'highlight', content: 'Review the interval.',
    metadata: { annotations: [
      { line: 1, start: 2, end: 6, quote: 'left', reason: 'Check the boundary.', category: 'possible_error' },
      { line: 1, start: 9, end: 12, quote: 'mid', reason: 'Explain this choice.', category: 'missing_justification' },
    ] },
  } });
  await until(() => document.querySelectorAll('.cm-content .annotation').length === 2);
  assert.equal(document.querySelector('.cm-content .annotation').textContent, 'left');
  const dismiss = await until(() => document.querySelector('#workspace [aria-label="Dismiss annotation"]'));
  dismiss.click();
  await until(() => document.querySelectorAll('.cm-content .annotation').length === 1);
  assert.equal(document.querySelector('.cm-line').textContent, workspace);
});

test('video visualization exposes native playback and chapter explanations', async t => {
  const {document,dispatch}=await setup(t);
  dispatch({type:'intervention',event_id:2,revision:1,result:{type:'visualization',content:'Find the lower bound.',metadata:{video:{title:'Search trace',url:'/api/videos/example',duration:15,chapters:[{start:0,end:15,text:'Keep searching for an earlier match.'}]}}}});
  const player=await until(()=>document.querySelector('.video-player'));
  assert.equal(player.querySelector('video').controls,true);
  assert.match(player.querySelector('video').src,/api\/videos\/example/);
  assert.match(player.textContent,/earlier match/);
  assert.ok(player.querySelector('select'));
});


test('manual tools still use the existing WebSocket request protocol', async t => {
  const { document, messages } = await setup(t);
  document.querySelector('[data-tool="hint"]').click();
  assert.deepEqual(messages[0], { type: 'student_action', action: 'hint_request', tool: 'hint' });
});


test('student plan drafts survive incoming updates and save against the latest version',async t=>{
  const {document,window,requests,dispatch}=await setup(t);
  const panel=document.querySelector('[aria-label="Learning plan"]');
  assert.match(panel.textContent,/Explain the invariant/);
  [...panel.querySelectorAll('button')].find(b=>b.textContent==='Edit plan').click();
  const input=await until(()=>panel.querySelector('input'));
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,'My edited objective');
  input.dispatchEvent(new window.Event('input',{bubbles:true}));
  await new Promise(r=>setTimeout(r,20));
  dispatch({type:'plan',plan:{objective:'Updated remotely',steps:['Review'],learning_goals:[]},plan_version:2});
  await until(()=>panel.textContent.includes('The saved plan changed'));
  assert.equal(input.value,'My edited objective');
  [...panel.querySelectorAll('button')].find(b=>b.textContent==='Keep draft against latest').click();
  await until(()=>!panel.querySelector('button[type="submit"]').disabled);
  panel.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
  await until(()=>panel.textContent.includes('Saved—used by future agent calls.'));
  assert.equal(requests[0].expected_version,2);
  assert.equal(requests[0].plan.objective,'My edited objective');
  assert.match(panel.textContent,/My edited objective/);
});

test('workspace submits only text and contains no node or edge editor',async t=>{
  const {document,messages}=await setup(t);
  assert.equal(document.querySelector('.diagram'),null);
  assert.equal(document.querySelector('.diagram-connect'),null);
  document.querySelector('#submit').click();
  const attempt=await until(()=>messages.find(message=>message.type==='problem_attempt'));
  assert.deepEqual(attempt.response,{text:workspace});
});

test('legacy local drafts restore their text without restoring diagrams',async t=>{
  const {document,messages}=await setup(t,{draft:{text:'Legacy text',diagram:{nodes:[{id:'a'}],edges:[]}}});
  await until(()=>document.querySelector('.cm-line')?.textContent==='Legacy text');
  document.querySelector('#submit').click();
  const attempt=await until(()=>messages.find(message=>message.type==='problem_attempt'));
  assert.deepEqual(attempt.response,{text:'Legacy text'});
  assert.equal(document.querySelector('.diagram'),null);
});

test('hypothesis panel reports origin, confidence, evidence and unavailable states',async t=>{
  const {document,dispatch}=await setup(t);
  const state={status:'ready',origin:'draft',question_id:'p1',plan_version:1,items:[{id:'h',concept:'Search',possible_gap:'May miss earlier matches.',confidence:.4,status:'tentative',diagnostic_focus:'Trace duplicates.',evidence:[{reference:'draft:p1:0',summary:'The draft stops at a match.'}]}]};
  dispatch({type:'hypotheses',state});
  const panel=document.querySelector('[aria-label="Possible knowledge gaps"]');
  await until(()=>panel.textContent.includes('40% confidence'));
  assert.match(panel.textContent,/based on a draft/);
  assert.match(panel.textContent,/The draft stops at a match/);
  dispatch({type:'hypotheses',state:{...state,origin:'submission',status:'unavailable',error:'Submission analysis unavailable.'}});
  await until(()=>panel.textContent.includes('Submission analysis unavailable.'));
  assert.match(panel.textContent,/assessed submission/);
  dispatch({type:'plan',plan:study.state.plan,plan_version:2});
  await until(()=>panel.textContent.includes('may be stale'));
});

test('next question retry retains feedback and never submits another assessment',async t=>{
  const {document,dispatch,requests,messages}=await setup(t);
  dispatch({...study,state:{...study.state,current_problem:{...study.state.current_problem,solved:true},question_generation_error:'Next question unavailable.',last_assessment:{correct:true,verified:false,method:'llm',reason:'Valid response.'}}});
  const retry=await until(()=>[...document.querySelectorAll('button')].find(b=>b.textContent==='Retry next question'));
  assert.match(document.querySelector('#assessment').textContent,/Valid response/);
  retry.click();
  await until(()=>document.querySelector('#problem').textContent==='Next question');
  assert.equal(requests[0].problem_id,'p1');
  assert.equal(messages.some(m=>m.type==='problem_attempt'),false);
});

test('idle observations run every ten seconds only for visible unanswered questions',async t=>{
  const {document,messages,intervals,dispatch}=await setup(t);
  const idle=intervals.find(timer=>timer.delay===10000);
  assert.ok(idle);
  Object.defineProperty(document,'hidden',{configurable:true,value:false});
  idle.callback();
  assert.equal(messages.filter(m=>m.action==='idle').length,1);
  Object.defineProperty(document,'hidden',{configurable:true,value:true});
  idle.callback();
  assert.equal(messages.filter(m=>m.action==='idle').length,1);
  Object.defineProperty(document,'hidden',{configurable:true,value:false});
  dispatch({...study,state:{...study.state,current_problem:{...study.state.current_problem,solved:true}}});
  idle.callback();
  assert.equal(messages.filter(m=>m.action==='idle').length,1);
});

test('background hypotheses update only the panel and grading activity stays compact',async t=>{
  const {document,dispatch}=await setup(t);
  dispatch({type:'hypotheses',state:{status:'empty',question_id:'p1',plan_version:1,items:[]}});
  await until(()=>document.querySelector('[aria-label="Possible knowledge gaps"]').textContent.includes('No possible'));
  assert.equal(document.querySelector('.memory-notice'),null);
  dispatch({type:'memory_activity',items:[{message:'Saved submission reflection and hypotheses.'}]});
  assert.equal(document.querySelector('.memory-notice'),null);
  assert.ok(document.querySelector('[aria-label="Agentic workflow"]'));
});

test('concept picker lists only unlocked concepts and web enhancement can be toggled',async t=>{
  const {document,dispatch,requests,window:w}=await setup(t);
  dispatch({...study,graph:{nodes:[
    {id:'Binary Search',label:'Binary Search',active:true,available:true},
    {id:'Sorting',label:'Sorting',available:true},
    {id:'Search Trees',label:'Search Trees',available:false}],
    edges:[{source:'Sorting',target:'Search Trees'}]}});
  const picker=await until(()=>document.querySelector('#concept-picker'));
  await until(()=>picker.options.length===2);
  assert.deepEqual([...picker.options].map(o=>o.value),['','Sorting']);
  picker.value='Sorting';
  picker.dispatchEvent(new w.Event('change',{bubbles:true}));
  await until(()=>document.querySelector('#problem').textContent==='Question about Sorting');
  assert.deepEqual(requests.find(r=>r.path.endsWith('/concept')),{path:'/api/sessions/1/concept',concept:'Sorting'});
  const enhance=document.querySelector('#web-enhance');
  assert.equal(enhance.checked,false);
  enhance.click();
  await until(()=>document.querySelector('#web-enhance').checked);
  assert.deepEqual(requests.find(r=>r.path.endsWith('/web-enhance')),{path:'/api/materials/m1/web-enhance',enabled:true});
});

test('new chat offers topic descriptions and optional uploads', async t => {
  const {document} = await setup(t, {openSession: false});
  assert.equal(document.querySelectorAll('#upload').length, 1);
  assert.equal(document.querySelector('#upload').multiple, true);
  assert.equal(document.querySelector('.app-header input[type=file]'), null);
  assert.ok(document.querySelector('#topic-description'));
  assert.match(document.querySelector('.new-chat-content').textContent,/Build my learning map/);
  assert.match(document.querySelector('.new-chat-content h1').textContent, /What would you like to learn/);
});


test('memory tab shows persisted memory and saves corrections',async t=>{
  const {document,window,requests}=await setup(t);
  document.querySelector('#memory-tab').click();
  const panel=await until(()=>document.querySelector('[aria-label="Learning memory"]'));
  await until(()=>panel.textContent.includes('Practiced search.'));
  assert.match(panel.textContent,/Short-term/);
  assert.match(panel.textContent,/50%/);
  [...panel.querySelectorAll('button')].find(button=>button.textContent==='Correct').click();
  const textarea=await until(()=>panel.querySelector('textarea'));
  Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set.call(textarea,'I practiced duplicates.');
  textarea.dispatchEvent(new window.Event('input',{bubbles:true}));
  await new Promise(resolve=>setTimeout(resolve,20));
  panel.querySelector('form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
  await until(()=>requests.some(request=>request.path.endsWith('/memory/corrections')));
  assert.equal(requests.at(-1).expected_revision,1);
  assert.equal(requests.at(-1).text,'I practiced duplicates.');
});

test('topic preparation shows selected sources and resolves ambiguity before opening study',async t=>{
  const {document,window,requests}=await setup(t,{openSession:false});
  const textarea=document.querySelector('#topic-description');
  Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set.call(textarea,'Teach me RAG');
  textarea.dispatchEvent(new window.Event('input',{bubbles:true}));
  await new Promise(resolve=>setTimeout(resolve,20));
  document.querySelector('.topic-form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
  const panel=await until(()=>document.querySelector('.preparation-panel'));
  await until(()=>panel.textContent.includes('Explains retrieval and grounding.'));
  assert.ok(panel.querySelector('a[href="https://example.org/rag"]'));
  [...panel.querySelectorAll('button')].find(button=>button.textContent.includes('Retrieval-augmented')).click();
  await until(()=>document.querySelector('#submit:not(:disabled)'));
  assert.equal(requests.find(request=>request.path.endsWith('/meaning')).index,0);
});


test('failed upload preparation reenables file selection and shows recovery guidance',async t=>{
  const {document,window}=await setup(t,{openSession:false,failedUpload:true});
  const upload=document.querySelector('#upload');
  Object.defineProperty(upload,'files',{configurable:true,value:[new window.File(['# Search'],'notes.md',{type:'text/markdown'})]});
  upload.dispatchEvent(new window.Event('change',{bubbles:true}));
  await until(()=>document.querySelector('.preparation-panel')?.textContent.includes('Choose your files again'));
  await until(()=>!document.querySelector('#upload').disabled);
  assert.match(document.querySelector('.preparation-panel').textContent,/provider rejected/);
});

function tracedRun(id,name,status,cursor) {
  return {id,name,status,cursor,started_at:'2026-11-12T12:00:00Z',trigger:{response_revision:12},
    spans:[{id:id+'-span',name:name+' agent',status,category:'agents',dependencies:[],consumed:['context-artifact'],produced:[],data:{response_revision:12}}],
    events:[{id:id+'-event',cursor,run_id:id,span_id:id+'-span',type:'context.received',category:'context',status:'completed',timestamp:'2026-11-12T12:00:00Z',data:{message:'Context received'}}],
    artifacts:[{id:'context-artifact',kind:'model_input'}],edges:[]};
}

test('workflow keeps overlapping runs visible, selects graph nodes, and loads public context on demand',async t=>{
  const observer=tracedRun('observer','Background review · Observer','running',1);
  const hypothesis=tracedRun('hypothesis','Background review · Hypothesis','queued',2);
  const data={runs:[observer,hypothesis],details:{observer,hypothesis},events:[],artifacts:{'context-artifact':{input:{snapshot:{response_revision:12,hypothesis_version:3},knowledge:[{id:'D1',text:'Document excerpt',url:'https://example.org/source'}]}}}};
  const {document,dispatch,workflowReads,window}=await setup(t,{workflowData:data});
  await until(()=>document.querySelectorAll('.workflow-run').length===2);
  assert.match(document.querySelector('.workflow-header').textContent,/2 active runs · 1 queued/);
  assert.equal(workflowReads.length,0);
  const node=document.querySelector('svg [role="button"][aria-label*="Observer"]');
  node.dispatchEvent(new window.KeyboardEvent('keydown',{key:'Enter',bubbles:true}));
  const inspector=await until(()=>document.querySelector('[aria-label="Activity inspector"]'));
  assert.ok(document.querySelector('.workflow-selected'));
  const evidence=[...inspector.querySelectorAll('details')].find(d=>d.querySelector('summary').textContent==='model input');
  evidence.open=true;
  evidence.dispatchEvent(new window.Event('toggle',{bubbles:true}));
  await until(()=>inspector.textContent.includes('Document excerpt'));
  assert.match(inspector.textContent,/hypothesis version3/);
  assert.ok(inspector.querySelector('a[href="https://example.org/source"]'));
  observer.status='completed';observer.spans[0].status='completed';
  const end={...observer.events[0],id:'observer-end',cursor:3,type:'run.completed'};
  observer.events.push(end);data.events.push(end);
  dispatch({type:'workflow_events',events:[end],watermark:3});
  await until(()=>document.querySelector('.workflow-header').textContent.includes('1 active runs'));
  assert.match(document.querySelector('.workflow-header').textContent,/Hypothesis · Queued/);
  dispatch({type:'workflow_events',events:[end],watermark:3});
  await new Promise(resolve=>setTimeout(resolve,60));
  assert.equal(document.querySelectorAll('[data-run-id="observer"]').length,1);
});

test('workflow filters and grouped reads preserve scroll position and show new activity',async t=>{
  const run=tracedRun('review','Background review','running',1);
  const event={id:'read-1',cursor:2,run_id:run.id,type:'memory.read',category:'memory',status:'completed',timestamp:'2026-11-12T12:00:00Z',data:{message:'Retrieved learning memory',memory_revision:8,artifact_id:'context-artifact'}};
  run.events.push(event,{...event,id:'read-2',cursor:3});
  const data={runs:[run],details:{review:run},events:[],artifacts:{'context-artifact':{memory_revision:8}}};
  const {document,dispatch,window}=await setup(t,{workflowData:data});
  await until(()=>document.querySelector('.workflow-run'));
  const filter=name=>[...document.querySelectorAll('.workflow-filters button')].find(b=>b.textContent===name);
  filter('Memory').click();
  await until(()=>document.querySelector('.workflow-steps').textContent.includes('2 recorded steps'));
  assert.doesNotMatch(document.querySelector('.workflow-steps').textContent,/Context received/);
  filter('Errors').click();
  await until(()=>document.querySelectorAll('.workflow-run').length===0);
  filter('All').click();
  await until(()=>document.querySelector('.workflow-run'));
  const scroll=document.querySelector('.workflow-scroll');
  Object.defineProperty(scroll,'scrollHeight',{configurable:true,value:1200});
  Object.defineProperty(scroll,'clientHeight',{configurable:true,value:200});
  scroll.scrollTop=120;scroll.dispatchEvent(new window.Event('scroll',{bubbles:true}));
  const next={...event,id:'read-3',cursor:4};run.events.push(next);data.events.push(next);
  dispatch({type:'workflow_events',events:[next],watermark:4});
  await until(()=>document.querySelector('.workflow-new'));
  assert.equal(scroll.scrollTop,120);
  document.querySelector('.workflow-new').click();
  await until(()=>!document.querySelector('.workflow-new'));
  assert.equal(scroll.scrollTop,1200);
});

test('workflow links teaching messages once and preserves legacy conversations',async t=>{
  const run=tracedRun('request','Student request','completed',1);
  run.events.push({id:'delivery',cursor:2,run_id:run.id,type:'application.intervention',category:'agents',status:'completed',timestamp:'2026-11-12T12:00:00Z',data:{event_id:50}});
  const data={runs:[run],details:{request:run},events:[],artifacts:{}};
  const {document,dispatch}=await setup(t,{workflowData:data});
  dispatch({type:'intervention',workflow_run_id:'request',event_id:50,revision:1,result:{type:'hint',content:'A traced teaching response.',metadata:{}}});
  await until(()=>document.querySelector('.workflow-run .intervention'));
  assert.equal([...document.querySelectorAll('.intervention')].filter(e=>e.textContent.includes('A traced teaching response.')).length,1);
  dispatch({type:'intervention',event_id:51,revision:1,result:{type:'hint',content:'An older teaching response.',metadata:{}}});
  await until(()=>document.querySelector('.workflow-legacy'));
  assert.match(document.querySelector('.workflow-legacy').textContent,/Detailed activity was not recorded/);
});


test('workflow pagination retains active runs and reconnect reconciles missed activity',async t=>{
  const old=tracedRun('old','Earlier request','completed',10);
  const middle=tracedRun('middle','Middle request','completed',20);
  const recent=tracedRun('recent','Recent request','completed',30);
  const queued=tracedRun('queued','Queued Observer','queued',1);
  const data={runs:[recent,middle,old,queued],details:{old,middle,recent,queued},events:[],artifacts:{}};
  const {document,window,disconnect,reopen}=await setup(t,{workflowData:data});
  await until(()=>document.querySelectorAll('.workflow-run').length===3);
  const scroll=document.querySelector('.workflow-scroll');
  Object.defineProperty(scroll,'scrollHeight',{configurable:true,get:()=>1000+document.querySelectorAll('.workflow-run').length*100});
  Object.defineProperty(scroll,'clientHeight',{configurable:true,value:200});
  scroll.scrollTop=120;scroll.dispatchEvent(new window.Event('scroll',{bubbles:true}));
  [...document.querySelectorAll('.workflow button')].find(b=>b.textContent==='Load earlier activity').click();
  await until(()=>document.querySelectorAll('.workflow-run').length===4);
  assert.equal(document.querySelectorAll('[data-run-id="queued"]').length,1);
  assert.equal(scroll.scrollTop,220);
  disconnect();
  await until(()=>document.querySelector('.conversation-heading').textContent.includes('Connecting'));
  const missed=tracedRun('missed','Missed while disconnected','completed',40);
  data.runs.unshift(missed);data.details.missed=missed;
  data.events.push(missed.events[0],missed.events[0]);
  await reopen();
  await until(()=>document.querySelector('[data-run-id="missed"]'));
  assert.equal(document.querySelectorAll('[data-run-id="missed"]').length,1);
  assert.equal(document.querySelectorAll('[data-run-id="old"]').length,1);
});
