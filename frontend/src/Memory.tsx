import { useEffect, useState } from 'react';
import type { MemoryItem, MemorySnapshot } from './types';
import Hypotheses from './Hypotheses';

export default function MemoryPanel({sessionId,active,version}: {sessionId:number;active:boolean;version:number}) {
  const [memory,setMemory]=useState<MemorySnapshot | null>(null);
  const [error,setError]=useState('');
  const [editing,setEditing]=useState<string | null>(null);
  const [text,setText]=useState('');
  const [busy,setBusy]=useState(false);
  const [refresh,setRefresh]=useState(0);
  useEffect(()=>{
    if(!active) return;
    let stopped=false;
    void fetch('/api/sessions/'+sessionId+'/memory').then(async response=>{
      const data=await response.json();
      if(!response.ok) throw Error(data.detail ?? 'Memory unavailable.');
      if(!stopped){setMemory(data);setError('');}
    }).catch(e=>{if(!stopped) setError(e.message);});
    return ()=>{stopped=true;};
  },[sessionId,active,version,refresh]);
  async function save(item?:MemoryItem) {
    if(!memory || !text.trim()) return;
    setBusy(true);
    try {
      const response=await fetch('/api/sessions/'+sessionId+'/memory/corrections',{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({expected_revision:memory.revision,
          action:!item ? 'add_goal' : item.kind==='misconception' ? 'dispute' : 'edit',item_id:item?.id ?? null,text:text.trim()})});
      const data=await response.json();
      if(!response.ok) {setRefresh(x=>x+1);throw Error(data.detail ?? 'Correction failed.');}
      setMemory(data);setEditing(null);setText('');setError('');
    }catch(e){setError((e as Error).message);}finally{setBusy(false);}
  }
  function editor(item?:MemoryItem) {
    return <form onSubmit={event=>{event.preventDefault();void save(item);}}>
      <label htmlFor="memory-correction">{item?.kind==='misconception' ? 'Explain your disagreement' : 'Your correction or learning goal'}</label>
      <textarea id="memory-correction" value={text} maxLength={2000} onChange={event=>setText(event.target.value)} />
      <button disabled={busy || !text.trim()}>Save</button><button type="button" onClick={()=>setEditing(null)}>Cancel</button>
    </form>;
  }
  function items(kind:string, readOnly=false) {
    const entries=memory?.items.filter(item=>item.kind===kind) ?? [];
    return entries.length ? <ul>{entries.map(item=><li key={item.id}>
      <p>{item.effective_text}</p>{item.concept && <small>{item.concept}</small>}
      {item.disputed && <p>Disputed by you: {item.student_note}</p>}
      <p className="muted">{item.updated_at ? 'Updated '+new Date(item.updated_at).toLocaleString() : 'Saved earlier; date unavailable'}
        {item.source_event_id ? ' · Evidence event '+item.source_event_id : ' · Earlier evidence reference unavailable'}</p>
      <details><summary>Original and correction history</summary><p>{item.original_text}</p>
        <ul>{item.edits.map((edit,i)=><li key={i}>{new Date(edit.at).toLocaleString()}: {edit.text}</li>)}</ul></details>
      {readOnly ? null : editing===item.id ? editor(item) : <button type="button" disabled={busy} onClick={()=>{
        setEditing(item.id);setText(item.kind==='misconception' ? item.student_note : item.effective_text);
      }}>{kind==='misconception' ? 'Dispute' : 'Correct'}</button>}
    </li>)}</ul> : <p className="muted">Nothing saved yet.</p>;
  }
  return <section aria-label="Learning memory"><h2>Learning memory</h2>
    <p>Your current session and what the tutor remembers across sessions. Estimates and possible misconceptions can be wrong.</p>
    {error && <p role="alert">{error}</p>}
    {!memory ? <p>Loading memory…</p> : <>
      <h3>Short-term · This session</h3>
      <dl><dt>Concept</dt><dd>{memory.short_term.concept || 'Not selected'}</dd>
        <dt>Question</dt><dd>{memory.short_term.question || 'No current question'}</dd>
        <dt>Saved response</dt><dd className="memory-response">{memory.short_term.response.text || 'No saved response yet'}</dd>
        <dt>Objective</dt><dd>{memory.short_term.plan.objective}</dd>
        <dt>Assessment</dt><dd>{memory.short_term.assessment_pending ? 'In progress' : memory.short_term.assessment?.reason || 'Not assessed yet'}</dd></dl>
      <Hypotheses state={memory.short_term.hypotheses} />
      <h3>Long-term · Across your sessions</h3>
      <h4>Estimated mastery</h4><ul>{Object.entries(memory.long_term.mastery).map(([concept,value])=><li key={concept}>{concept}: {Math.round(value*100)}%</li>)}</ul>
      <h4>Your learning goals</h4>{items('student_goal')}
      {editing==='new' ? editor() : <button type="button" onClick={()=>{setEditing('new');setText('');}}>Add learning goal</button>}
      <h4>Suggested next goals</h4>{items('agent_goal')}
      <h4>Concept summaries</h4><p className="muted">One rolling summary per concept, rewritten after each graded answer. Only the concepts in your current study map are shared with the tutor.</p>{items('summary')}
      <h4>Learner profile</h4>{items('profile')}
      <h4>Assessed learning hypotheses</h4><Hypotheses state={{status:'ready',items:memory.long_term.hypotheses,origin:'submission'}} />
      <h3>Possible misconceptions</h3><p>These are provisional observations. Add a dispute if the evidence misrepresents your understanding.</p>{items('misconception')}
      {!!memory.items.some(item=>item.kind==='archived') && <details><summary>Archived notes (not shared with the tutor)</summary>{items('archived', true)}</details>}
    </>}
  </section>;
}
