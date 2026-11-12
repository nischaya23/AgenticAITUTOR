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

async function setup(t, { draft, authenticated = true, openSession = true, failedUpload = false,
  uploadGate, savedPreparation, sessionGate } = {}) {
  const dom = new JSDOM(html, { url: 'http://localhost:8000', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window;
  const messages = [];
  const requests = [];
  let socket;
  let operationReady=false;
  const memorySnapshot = {
    short_term: {question:'Find the first 2.', response:{text:workspace}},
    long_term: {
      mastery:{'binary search':.5},
      concept_memory:{'binary search':{name:'Binary Search',summary:'Practiced search.',next_goal:'Trace duplicate values.'}},
      hypotheses:[{id:'first',concept:'Binary Search',status:'tentative',possible_gap:'May stop at the first midpoint match.'}],
      learner_profile:'Prefers worked examples.',
      student_goals:['Explain the boundary invariant.'],
    },
  };
  const intervals=[];
  const setInterval=w.setInterval.bind(w);
  w.setInterval=(callback,delay,...args)=>{intervals.push({callback,delay});return setInterval(callback,delay,...args);};
  w.localStorage.setItem('studyroom-auto-session-1', '1');
  if (savedPreparation) w.sessionStorage.setItem('studyroom-job-1', savedPreparation);
  if (draft) w.localStorage.setItem('studyroom-draft-1-1', JSON.stringify({questionId:'p1',response:draft}));
  w.DOMMatrixReadOnly = class { m22 = 1; };
  w.fetch = async (url, options) => {
    if (url === '/api/auth/me') return { ok: authenticated, status: authenticated ? 200 : 401,
      json: async () => authenticated ? {user: {id: 1, email: 'student@example.com'}} : {detail: 'Please log in.'} };
    if (url === '/api/sessions' && options?.method==='POST') {
      requests.push({path:url,...JSON.parse(options.body ?? '{}')});
      await sessionGate;
      return {ok:true,json:async()=>({id:3})};
    }
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
    if(url==='/api/material-jobs') {
      await uploadGate;
      operationReady = !failedUpload;
      return {ok:true,json:async()=>({id:'j1'})};
    }
    if(url==='/api/material-jobs/topic'){const body=JSON.parse(options.body);requests.push({path:url,...body});return {ok:true,json:async()=>({id:'j1'})};}
    if(url==='/api/material-jobs/j1/meaning'){requests.push({path:url,...JSON.parse(options.body)});operationReady=true;return {ok:true,json:async()=>({id:'j1'})};}
    if(url==='/api/operations/j1') return {ok:true,json:async()=>({id:'j1',kind:failedUpload?'upload':'topic',error:failedUpload?'The provider rejected graph preparation.':undefined,status:failedUpload?'failed':operationReady?'ready':'needs_clarification',result:operationReady?{id:'m1'}:{type:'choices',meanings:[{label:'Retrieval-augmented generation',description:'Grounding answers in documents.',query:'RAG AI'}]},
      events: (failedUpload ? ['reading', 'building_graph'] : ['interpreting_topic', 'searching', 'selected']).map((kind, i) => ({id:i+1, kind}))})};
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
  return { document, messages, requests, intervals, disconnect:()=>socket.onclose({code:1006}), reopen:()=>socket.onopen(), window: w, dispatch: data => socket.onmessage({ data: JSON.stringify(data) }),
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

test('visualization shows a React Flow slideshow with step controls', async t => {
  const {document,dispatch}=await setup(t);
  dispatch({type:'intervention',event_id:2,revision:1,result:{type:'visualization',content:'Find the lower bound.',
    metadata:{visualization:{kind:'slideshow',title:'Search trace',nodes:[{id:'full',label:'Full interval'},{id:'left',label:'Earlier match'}],
      edges:[{source:'full',target:'left'}],steps:[{explanation:'Start with the full interval.',active_nodes:['full']},
        {explanation:'Keep searching for an earlier match.',active_nodes:['left']}]}}}});
  const player=await until(()=>document.querySelector('.visual-player'));
  const button = name => [...player.querySelectorAll('button')].find(item=>item.textContent===name);
  assert.match(player.textContent,/Step 1 of 2/);
  assert.match(player.querySelector('.flow-active').textContent,/Full interval/);
  assert.equal(player.querySelector('.flow-active').style.visibility, 'visible');
  button('Next').click();
  await until(()=>player.textContent.includes('Step 2 of 2'));
  assert.match(player.querySelector('.flow-active').textContent,/Earlier match/);
  button('Previous').click();
  await until(()=>player.textContent.includes('Step 1 of 2'));
  button('Play').click();
  await until(()=>player.textContent.includes('Step 2 of 2'));
  button('Restart').click();
  await until(()=>player.textContent.includes('Step 1 of 2'));
  assert.equal(player.querySelector('video'),null);
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
  assert.ok(document.querySelector('[aria-label="Tutor chat"]'));
  assert.equal(document.querySelector('.workflow'), null);
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


test('memory tab shows long-term memory in a read-only concept table',async t=>{
  const {document}=await setup(t);
  document.querySelector('#memory-tab').click();
  const table=await until(()=>document.querySelector('#memory-panel table'));
  assert.equal(table.tBodies[0].rows.length, 1);
  assert.match(table.textContent,/Binary Search/);
  assert.match(table.textContent,/50% estimated mastery/);
  assert.match(table.textContent,/Practiced search/);
  assert.match(table.textContent,/Possible gap · May stop at the first midpoint match/);
  assert.match(table.textContent,/Trace duplicate values/);
  assert.match(table.textContent,/Prefers worked examples/);
  assert.match(table.textContent,/Explain the boundary invariant/);
  const panel = document.querySelector('#memory-panel');
  assert.doesNotMatch(panel.textContent,/Find the first 2/);
  assert.equal(panel.querySelector('button, input, textarea, form'),null);
});

test('topic preparation shows a short flow and resolves ambiguity before opening study',async t=>{
  const {document,window,requests}=await setup(t,{openSession:false});
  const textarea=document.querySelector('#topic-description');
  Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype,'value').set.call(textarea,'Teach me RAG');
  textarea.dispatchEvent(new window.Event('input',{bubbles:true}));
  await new Promise(resolve=>setTimeout(resolve,20));
  document.querySelector('.topic-form').dispatchEvent(new window.Event('submit',{bubbles:true,cancelable:true}));
  const panel=await until(()=>{
    const preparation=document.querySelector('.preparation-panel');
    return preparation?.textContent.includes('Choose a subject') && preparation;
  });
  assert.deepEqual([...panel.querySelectorAll('.activity-flow li')].map(step=>step.dataset.state), ['done','done','done','paused']);
  assert.match(panel.textContent,/Searching sources \(Research Agent\)/);
  assert.match(panel.textContent,/Reviewing sources \(Research Agent\)/);
  [...panel.querySelectorAll('button')].find(button=>button.textContent.includes('Retrieval-augmented')).click();
  await until(()=>document.querySelector('#submit:not(:disabled)'));
  assert.equal(requests.find(request=>request.path.endsWith('/meaning')).index,0);
});


test('new chat ignores saved preparation and shows only the upload step until the upload finishes',async t=>{
  const {promise:uploadGate,resolve:finishUpload}=Promise.withResolvers();
  const {document,window}=await setup(t,{openSession:false,failedUpload:true,uploadGate,savedPreparation:'j1'});
  assert.equal(document.querySelector('[aria-label="Preparation progress"]'),null);
  const upload=document.querySelector('#upload');
  Object.defineProperty(upload,'files',{configurable:true,value:[new window.File(['# Search'],'notes.md',{type:'text/markdown'})]});
  upload.dispatchEvent(new window.Event('change',{bubbles:true}));
  const uploading=await until(()=>document.querySelector('[aria-label="Preparation progress"]'));
  assert.deepEqual([...uploading.children].map(step=>step.textContent),['Uploading files']);
  assert.equal(uploading.firstElementChild.dataset.state,'working');
  finishUpload();
  await until(()=>document.querySelector('.preparation-panel')?.textContent.includes('Choose your files again'));
  await until(()=>!document.querySelector('#upload').disabled);
  assert.deepEqual([...document.querySelectorAll('.preparation-panel .activity-flow li')].map(step=>step.dataset.state),['done','done','paused']);
  assert.match(document.querySelector('.preparation-panel').textContent,/Reading material \(Python\)/);
  assert.match(document.querySelector('.preparation-panel').textContent,/Building your learning map \(Knowledge Agent\)/);
  assert.match(document.querySelector('.preparation-panel').textContent,/provider rejected/);
});

test('an upload opens one chat while the automatic handoff is in progress', async t => {
  const {promise:sessionGate,resolve:allowSession}=Promise.withResolvers();
  const {document,window,requests}=await setup(t,{openSession:false,sessionGate});
  const upload=document.querySelector('#upload');
  Object.defineProperty(upload,'files',{value:[new window.File(['# Search'],'notes.md',{type:'text/markdown'})]});
  upload.dispatchEvent(new window.Event('change',{bubbles:true}));
  const opening=await until(()=>document.querySelector('.preparation-panel button:disabled'));
  opening.click();
  opening.click();
  assert.deepEqual(requests.filter(request=>request.path==='/api/sessions'),[{path:'/api/sessions',preparation_id:'j1'}]);
  allowSession();
  await until(()=>document.querySelector('#submit:not(:disabled)'));
  assert.equal(requests.filter(request=>request.path==='/api/sessions').length,1);
});

test('tutor progress shows completed and current steps alongside the conversation', async t => {
  const {document, dispatch} = await setup(t);
  const steps = () => [...document.querySelectorAll('[aria-label="Tutor progress"] li')];
  const plan = {run_id:'request', span_id:'plan', type:'span.started', status:'running',
    data:{name:'Planner · Create plan'}};
  const reply = {...plan, span_id:'reply', data:{name:'Tutor · Generate response'}};
  dispatch({type:'workflow_events', events:[plan], watermark:1});
  await until(() => steps()[0]?.textContent.includes('Planning your next step (Planner Agent)'));
  assert.equal(steps()[0].getAttribute('aria-current'), 'step');

  dispatch({type:'workflow_events', events:[{...plan, type:'span.completed', status:'completed'}, reply], watermark:3});
  await until(() => steps()[1]?.textContent.includes('Writing a reply (Tutor Agent)'));
  assert.deepEqual(steps().map(step => step.dataset.state), ['done', 'working']);

  dispatch({type:'workflow_events', events:[{...reply, type:'span.completed', status:'completed'}], watermark:4});
  dispatch({type:'intervention', workflow_run_id:'request', event_id:50, revision:1,
    result:{type:'hint', content:'Try narrowing the search range.', metadata:{}}});
  await until(() => document.querySelector('#interventions').textContent.includes('Try narrowing the search range.'));
  assert.deepEqual(steps().map(step => step.dataset.state), ['done', 'done']);
  assert.ok(document.querySelector('.chat-composer'));
});
