/* DOM rendering tests; backend transport is mocked, no browser or API key required. */
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.join(__dirname,'..');

async function setup(){
  const dom=new JSDOM(fs.readFileSync(path.join(root,'frontend/index.html'),'utf8'),{
    url:'http://localhost:8000',runScripts:'outside-only',pretendToBeVisual:true
  });
  const w=dom.window,messages=[],problem={id:'p1',prompt:'Find the first 2.',concept:'Binary Search'};
  let ws;
  w.fetch=async url=>({ok:true,json:async()=>{
    if(url==='/api/health')return {demo_mode:true,configured:false};
    if(url==='/api/materials')return [{id:'m1',name:'notes.md',chunks:[{id:'m1:0',text:'An invariant stays true.',source:'notes.md'}]}];
    return {id:1,state:{current_concept:'Binary Search',current_problem:problem,
      workspace:'',proactive_enabled:true,plan:{},ended:false},history:[]};
  }});
  w.WebSocket=class {
    static OPEN=1;
    readyState=1;
    constructor(){ws=this;queueMicrotask(()=>this.onopen?.());}
    send(raw){messages.push(JSON.parse(raw));}
    close(){}
  };
  w.setInterval=()=>0;
  w.eval(fs.readFileSync(path.join(root,'frontend/app.js'),'utf8'));
  await new Promise(resolve=>setTimeout(resolve,30));
  return {dom,w,messages,get ws(){return ws;},dispatch(data){ws.onmessage({data:JSON.stringify(data)});}};
}

test('renders all five intervention types and a structured graph',async()=>{
  const env=await setup();
  try{
    const metadata={concept:'Binary Search',nodes:[{id:'a',label:'Start'},{id:'b',label:'Finish'}],
      edges:[{source:'a',target:'b'}],steps:['Halve the interval.']};
    for(const type of ['hint','explanation','highlight','visualization','guiding_question'])
      env.dispatch({type:'intervention',event_id:1,proactive:false,result:{type,content:'A useful next step.',metadata}});
    assert.equal(env.w.document.querySelectorAll('.message.tutor').length,5);
    assert.equal(env.w.document.querySelectorAll('svg rect').length,2);
    assert.equal(env.w.document.querySelector('.trace li').textContent,'Halve the interval.');
  }finally{env.dom.window.close();}
});

test('Unicode highlight offsets match source code points',async()=>{
  const env=await setup();
  try{
    const d=env.w.document;
    d.getElementById('workspace').value='😀 invariant';
    env.dispatch({type:'intervention',result:{type:'highlight',content:'Focus here.',metadata:{
      concept:'x',target:'workspace',source_id:'workspace',start:2,end:11,quote:'invariant'}}});
    d.querySelector('.message button').click();
    assert.equal(d.querySelector('#highlight-preview mark').textContent,'invariant');
    assert.equal(d.getElementById('workspace').selectionStart,3);
    assert.equal(d.getElementById('workspace').selectionEnd,12);
  }finally{env.dom.window.close();}
});

test('edits flush before explicit tool requests and attempts reference the problem',async()=>{
  const env=await setup();
  try{
    const d=env.w.document;
    d.getElementById('workspace').value='My reasoning';
    d.querySelector('[data-tool=hint]').click();
    assert.equal(env.messages[0].type,'student_action');
    assert.equal(env.messages[1].tool,'hint');
    d.getElementById('answer').value='1';
    d.getElementById('attempt-form').dispatchEvent(new env.w.Event('submit',{cancelable:true}));
    assert.equal(env.messages.at(-1).problem_id,'p1');
    d.getElementById('check-work').click();
    assert.equal(env.messages.at(-1).assessment_mode,'reasoning');
  }finally{env.dom.window.close();}
});

test('messages render as text and unsent edits suppress proactive output',async()=>{
  const env=await setup();
  try{
    const d=env.w.document,payload='<img src=x onerror=alert(1)>';
    env.dispatch({type:'intervention',result:{type:'explanation',content:payload,metadata:{}}});
    assert.equal(d.querySelector('.content').textContent,payload);
    assert.equal(d.querySelectorAll('.message img').length,0);
    d.getElementById('workspace').value='new work';
    d.getElementById('workspace').dispatchEvent(new env.w.Event('input'));
    env.dispatch({type:'intervention',proactive:true,result:{type:'hint',content:'Stale hint',metadata:{}}});
    assert.equal(d.querySelectorAll('.message.tutor').length,1);
  }finally{env.dom.window.close();}
});
