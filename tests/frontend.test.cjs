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

async function setup(t, { draft } = {}) {
  const dom = new JSDOM(html, { url: 'http://localhost:8000', runScripts: 'outside-only', pretendToBeVisual: true });
  t.after(() => dom.window.close());
  const w = dom.window;
  const messages = [];
  const requests = [];
  let socket;
  const intervals=[];
  const setInterval=w.setInterval.bind(w);
  w.setInterval=(callback,delay,...args)=>{intervals.push({callback,delay});return setInterval(callback,delay,...args);};
  w.localStorage.setItem('studyroom-auto-session', '1');
  if (draft) w.localStorage.setItem('studyroom-draft-1', JSON.stringify({questionId:'p1',response:draft}));
  w.DOMMatrixReadOnly = class { m22 = 1; };
  w.fetch = async (url, options) => {
    if(options?.method === 'PUT'){ const body=JSON.parse(options.body); requests.push(body); return {ok:true,json:async()=>({type:'plan',plan:body.plan,plan_version:body.expected_version+1})}; }
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
  await until(() => document.querySelector('#submit:not(:disabled)'));
  return { document, messages, requests, intervals, window: w, dispatch: data => socket.onmessage({ data: JSON.stringify(data) }) };
}

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

test('binary search playback shows interval, controls and explanations', async t => {
  const { document, dispatch } = await setup(t);
  dispatch({ type: 'intervention', event_id: 2, revision: 1, result: {
    type: 'visualization', content: 'Find the lower bound.', metadata: { visualization: {
      kind: 'binary_search', title: 'Search trace', array: [2], target: 2,
      steps: [
        { left: 0, right: 1, mid: 0, comparison: 'equal', explanation: 'Found a match.' },
        { left: 0, right: 0, mid: null, comparison: 'complete', explanation: 'Finish at index 0.' },
      ],
    } },
  } });
  const player = await until(() => document.querySelector('.visual-player'));
  const button = name => [...player.querySelectorAll('button')].find(item => item.textContent === name);
  assert.match(player.textContent, /Interval \[0, 1\)/);
  button('Next').click();
  await until(() => player.textContent.includes('Step 2 of 2'));
  button('Previous').click();
  await until(() => player.textContent.includes('Step 1 of 2'));
  button('Play').click();
  await until(() => player.textContent.includes('Step 2 of 2'));
  button('Restart').click();
  await until(() => player.textContent.includes('Step 1 of 2'));
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

test('unified workspace edits diagrams and submits the exact text and graph together', async t => {
  const {document,messages,window}=await setup(t);
  assert.equal(document.querySelector('#answer'),null);
  const response=document.querySelector('[aria-label="Your response"]');
  const button=name=>[...response.querySelectorAll('button')].find(b=>b.textContent===name);
  button('Add node').click();
  await until(()=>response.querySelectorAll('.diagram-nodes>li').length===1);
  button('Add node').click();
  await until(()=>response.querySelectorAll('.diagram-nodes>li').length===2);
  const selects=response.querySelectorAll('.diagram-connect select');
  selects[0].value=selects[0].options[1].value;
  selects[0].dispatchEvent(new window.Event('change',{bubbles:true}));
  selects[1].value=selects[1].options[2].value;
  selects[1].dispatchEvent(new window.Event('change',{bubbles:true}));
  await until(()=>!button('Connect nodes').disabled);
  button('Connect nodes').click();
  await until(()=>response.querySelector('[aria-label="Response diagram arrows"] li'));
  document.querySelector('#submit').click();
  const attempt=await until(()=>messages.find(m=>m.type==='problem_attempt'));
  assert.equal(attempt.response.text,workspace);
  assert.equal(attempt.response.diagram.nodes.length,2);
  assert.equal(attempt.response.diagram.edges.length,1);
  assert.equal(attempt.content,undefined);
  assert.equal(messages.find(m=>m.action==='edit').response.diagram.edges.length,1);
});

test('diagram annotations stay inside the response and clear after renaming a node', async t=>{
  const draft={text:'',diagram:{nodes:[{id:'a',label:'Student node',position:{x:0,y:0}}],edges:[]}};
  const {document,dispatch,window}=await setup(t,{draft});
  dispatch({type:'ack',event_id:1,event_type:'student_action',action:'edit',revision:2});
  dispatch({type:'intervention',event_id:2,revision:2,proactive:false,result:{type:'highlight',content:'Check direction.',metadata:{annotations:[],diagram_annotations:[{target:'node',id:'a',reason:'This step may be reversed.'}]}}});
  const response=document.querySelector('[aria-label="Your response"]');
  await until(()=>response.querySelector('.diagram-issue'));
  assert.match(response.textContent,/This step may be reversed/);
  assert.equal(document.querySelector('#graph .diagram-issue'),null);
  const input=response.querySelector('input[aria-label="Label for Student node"]');
  Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set.call(input,'Renamed');
  input.dispatchEvent(new window.Event('input',{bubbles:true}));
  await until(()=>!response.querySelector('.diagram-issue'));
  assert.match(window.localStorage.getItem('studyroom-draft-1'),/Renamed/);
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
  const notification=await until(()=>document.querySelector('.memory-notice'));
  assert.equal(notification.open,false);
  assert.match(notification.textContent,/Saved submission reflection/);
});
