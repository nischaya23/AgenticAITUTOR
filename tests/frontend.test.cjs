const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {JSDOM}=require('jsdom');
const root=path.join(__dirname,'..');
const baseStudy=()=>({type:'study_state',mastery_target:.75,
  state:{material_id:'m1',current_concept:'Binary Search',current_problem:{id:'p1',prompt:'Find the first 2.'},workspace:'',ended:false},
  material:{id:'m1',name:'course.md',chunks:[{id:'m1:0',text:'Keep an invariant.'}]},
  graph:{nodes:[{id:'Binary Search',label:'Binary Search',mastery:null,active:true}],edges:[]}});
async function setup(){
  const dom=new JSDOM(fs.readFileSync(path.join(root,'frontend/index.html'),'utf8'),{url:'http://localhost:8000',runScripts:'outside-only'});
  const w=dom.window,messages=[];let ws;
  w.localStorage.setItem('studyroom-auto-session','1');
  w.fetch=async()=>({ok:true,status:200,text:async()=>JSON.stringify(baseStudy())});
  w.WebSocket=class {static OPEN=1;readyState=1;constructor(){ws=this;queueMicrotask(()=>this.onopen?.());}send(raw){messages.push(JSON.parse(raw));}close(){}};
  w.setInterval=()=>0;
  w.eval(fs.readFileSync(path.join(root,'frontend/app.js'),'utf8'));
  await new Promise(resolve=>setTimeout(resolve,30));
  return {dom,w,messages,dispatch(message){ws.onmessage({data:JSON.stringify(message)});}};
}
test('minimal controls and graph with unassessed mastery',async()=>{
  const e=await setup();try{
    const d=e.w.document;
    assert.equal(d.querySelectorAll('button').length,6);
    assert.equal(d.querySelectorAll('[data-tool]').length,5);
    assert.equal(d.getElementById('problem').textContent,'Find the first 2.');
    assert.match(d.getElementById('mastery').textContent,/Not assessed yet/);
    assert.equal(d.querySelectorAll('#graph svg rect').length,1);
  }finally{e.dom.window.close();}
});
test('all five interventions render automatically, including highlights and graphs',async()=>{
  const e=await setup();try{
    const d=e.w.document;d.getElementById('workspace').value='😀 invariant';
    for(const type of ['hint','explanation','guiding_question'])e.dispatch({type:'intervention',result:{type,content:'Try one step.',metadata:{}}});
    e.dispatch({type:'intervention',result:{type:'highlight',content:'Focus here.',metadata:{target:'workspace',source_id:'workspace',start:2,end:11,quote:'invariant'}}});
    e.dispatch({type:'intervention',result:{type:'visualization',content:'A trace.',metadata:{nodes:[{id:'a',label:'Start'}],edges:[],steps:['Halve the interval.']}}});
    assert.equal(d.querySelectorAll('.intervention').length,5);
    assert.equal(d.querySelector('#highlight-preview mark').textContent,'invariant');
    assert.equal(d.querySelectorAll('.intervention svg rect').length,1);
    assert.equal(d.querySelector('.intervention li').textContent,'Halve the interval.');
  }finally{e.dom.window.close();}
});
test('answer submission flushes edits and prevents duplicate submissions',async()=>{
  const e=await setup();try{
    const d=e.w.document;d.getElementById('workspace').value='My reasoning';d.getElementById('answer').value='1';
    const form=d.getElementById('answer-form');form.dispatchEvent(new e.w.Event('submit',{cancelable:true}));
    form.dispatchEvent(new e.w.Event('submit',{cancelable:true}));
    assert.equal(e.messages.length,2);assert.equal(e.messages[0].action,'edit');assert.equal(e.messages[1].problem_id,'p1');
    assert.equal(d.getElementById('submit').disabled,true);
  }finally{e.dom.window.close();}
});
test('progress updates preserve edits and next question clears old work',async()=>{
  const e=await setup();try{
    const d=e.w.document;d.getElementById('workspace').value='Unsent work';
    const same=baseStudy();same.graph.nodes[0].mastery=.6;e.dispatch(same);
    assert.equal(d.getElementById('workspace').value,'Unsent work');
    assert.match(d.getElementById('mastery').textContent,/60%/);
    const next=baseStudy();next.state.current_problem={id:'p2',prompt:'Next task'};e.dispatch(next);
    assert.equal(d.getElementById('workspace').value,'');assert.equal(d.getElementById('problem').textContent,'Next task');
  }finally{e.dom.window.close();}
});
test('unsafe content remains text and unsent edits suppress stale hints',async()=>{
  const e=await setup();try{
    const d=e.w.document;
    e.dispatch({type:'intervention',result:{type:'explanation',content:'<img src=x onerror=alert(1)>',metadata:{}}});
    assert.equal(d.querySelectorAll('.intervention img').length,0);
    d.getElementById('workspace').value='Changed';d.getElementById('workspace').dispatchEvent(new e.w.Event('input'));
    e.dispatch({type:'intervention',proactive:true,result:{type:'hint',content:'Stale',metadata:{}}});
    assert.equal(d.querySelectorAll('.intervention').length,1);
  }finally{e.dom.window.close();}
});
