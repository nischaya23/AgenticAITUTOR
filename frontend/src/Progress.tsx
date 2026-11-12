import { useEffect, useRef, useState } from 'react';
import Workflow from './Workflow';
import type { Operation, ProgressEvent, Video } from './types';

async function request<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : undefined);
  const data = await response.json();
  if (!response.ok) throw Error(data.detail ?? 'Request failed.');
  return data;
}

function useOperation(id: string) {
  const [state,setState] = useState<Operation | null>(null);
  const [error,setError] = useState('');
  const [refresh,setRefresh] = useState(0);
  const [version,setVersion] = useState(0);
  useEffect(() => {
    let stopped=false, stream: EventSource | undefined, timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const next=await request<Operation>('/api/operations/'+id);
        if (stopped) return;
        setState(next);setVersion(value=>value+1);setError('');
        if (next.status !== 'running') {stream?.close();return;}
        if (!stream && typeof EventSource !== 'undefined') {
          const after=next.events.at(-1)?.id ?? 0;
          stream=new EventSource('/api/operations/'+id+'/events?after='+after+'&workflow_after=0');
          stream.onmessage=() => {void read();};
          stream.onerror=() => {stream?.close();stream=undefined;clearTimeout(timer);timer=setTimeout(read,2000);};
        } else if (!stream) timer=setTimeout(read,1000);
      } catch (e) {if (!stopped) {setError((e as Error).message);timer=setTimeout(read,3000);}}
    }
    void read();
    return () => {stopped=true;stream?.close();clearTimeout(timer);};
  },[id,refresh]);
  return {state,error,version,reload:()=>setRefresh(x=>x+1)};
}

export function ResearchTimeline({events}: {events:ProgressEvent[]}) {
  return <details className="research-timeline" open><summary>Research and preparation</summary>
    <ol aria-live="polite">{events.map((event,i)=><li key={event.id ?? i} className={'research-'+event.kind}>
      <strong>{event.kind.replaceAll('_',' ')}</strong>{event.query && <span>: {event.query}</span>}
      {event.url && <p><a href={event.url} target="_blank" rel="noopener noreferrer">{event.title || event.url}</a></p>}
      {(event.reason || event.message) && <p>{event.reason || event.message}</p>}
      {event.quote && <blockquote>{event.quote}</blockquote>}
    </li>)}</ol>
  </details>;
}

export function PreparationPanel({id,onReady,onWaiting}: {id:string;onReady:(materialId:string)=>Promise<void>;onWaiting?:()=>void}) {
  const {state,error,reload,version}=useOperation(id);
  const opened=useRef('');
  const [notice,setNotice]=useState('');
  const [acting,setActing]=useState(false);
  async function act(path:string,body:object={}) {
    setActing(true);setNotice('');
    try {await request(path,body);reload();} catch(e) {setNotice((e as Error).message);} finally {setActing(false);}
  }
  useEffect(()=>{
    if(state && ['failed','interrupted','needs_clarification'].includes(state.status)) onWaiting?.();
    if (state?.status==='ready' && state.result?.id && opened.current!==id) {
      opened.current=id; void onReady(state.result.id);
    }
  },[state,id,onReady]);
  return <section className="preparation-panel" aria-label="Learning preparation">
    <p role="status">{state?.status==='running' ? 'Preparing your learning map…' : state?.status?.replaceAll('_',' ') ?? 'Connecting to preparation…'}</p>
    <h2>Agentic workflow</h2><Workflow operationId={id} refreshKey={version} />
    {state?.status==='needs_clarification' && <div><p>Which subject do you mean?</p>
      {state.result?.meanings?.map((meaning,index)=><button type="button" key={index} disabled={acting}
        onClick={()=>void act('/api/material-jobs/'+id+'/meaning',{index})}>{meaning.label} — {meaning.description}</button>)}</div>}
    {(error || notice || state?.error) && <p role="alert">{error || notice || state?.error}</p>}
    {(state?.status==='failed' || state?.status==='interrupted') && state.kind==='topic' && <button type="button" disabled={acting}
      onClick={()=>void act('/api/material-jobs/'+id+'/retry')}>Retry topic research</button>}
    {(state?.status==='failed' || state?.status==='interrupted') && state.kind==='upload' && <p>Choose your files again above to retry preparation.</p>}
    {state?.status==='ready' && state.result?.id && <button type="button" onClick={()=>void onReady(state.result!.id!)}>Open study session</button>}
  </section>;
}

export function VideoPlayer({video}: {video:Video}) {
  const player=useRef<HTMLVideoElement>(null);
  return <section className="video-player" aria-label={video.title}>
    <h3>{video.title}</h3><video ref={player} controls preload="metadata" src={video.url} aria-label={video.title}><track kind="captions" src={video.url+'/captions'} srcLang="en" label="Explanation" default /></video>
    <label>Playback speed <select defaultValue="1" onChange={event=>{if(player.current) player.current.playbackRate=Number(event.target.value);}}>
      {[.5,1,1.5,2].map(speed=><option key={speed} value={speed}>{speed}×</option>)}</select></label>
    <details><summary>Chapters and explanation</summary><ol>{video.chapters.map((chapter,i)=><li key={i}>
      <button type="button" onClick={()=>{if(player.current) player.current.currentTime=chapter.start;}}>{chapter.start.toFixed(0)}s</button> {chapter.text}
    </li>)}</ol></details>
  </section>;
}

export function VideoJob({id,title}: {id:string;title:string}) {
  const [currentId,setCurrentId]=useState(id);
  const {state,error}=useOperation(currentId);
  const [retryError,setRetryError]=useState('');
  if(state?.status==='ready' && !state.result?.discarded) return null;
  return <section className="video-job" aria-label="Animation progress"><strong>{title}</strong>{state?.content && <p>{state.content}</p>}
    <p role="status">{state?.result?.discarded ? 'Animation discarded because your work changed.' : state?.status==='failed' ? 'Video unavailable; your written explanation is saved.' : state?.events.at(-1)?.message ?? 'Preparing your animation…'}</p>
    {(error || state?.error || retryError) && <p>{error || state?.error || retryError}</p>}
    {(state?.status==='failed' || state?.status==='interrupted') && <button type="button" onClick={async()=>{
      try {const next=await request<{operation_id:string}>('/api/operations/'+currentId+'/retry-video',{});setCurrentId(next.operation_id);}
      catch(e){setRetryError((e as Error).message);}
    }}>Retry animation</button>}
  </section>;
}
