import { useEffect, useRef, useState } from 'react';
import ActivityFlow, { type FlowStep, type StepDescription } from './ActivityFlow';
import type { Operation, Video } from './types';

const reviewingSources = { label: 'Reviewing sources', actor: 'Research Agent' };
const preparationLabels: Record<string, StepDescription> = {
  reading: { label: 'Reading material', actor: 'Python' },
  interpreting_topic: { label: 'Interpreting your topic', actor: 'Topic interpretation model' },
  searching: { label: 'Searching sources', actor: 'Research Agent' },
  candidate: reviewingSources,
  fetched: reviewingSources,
  selected: reviewingSources,
  building_graph: { label: 'Building your learning map', actor: 'Knowledge Agent' },
  checking_evidence: { label: 'Checking evidence', actor: 'Knowledge Agent' },
};

const videoLabels: Record<string, StepDescription> = {
  rendering: { label: 'Rendering the animation', actor: 'Pillow / FFmpeg' },
  repairing: { label: 'Repairing the animation script', actor: 'Animation model' },
  cache_hit: { label: 'Loading the saved animation', actor: 'Video renderer' },
};

async function request<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(path, body ? {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)} : undefined);
  const data = await response.json();
  if (!response.ok) throw Error(data.detail ?? 'Request failed.');
  return data;
}

function useOperation(id: string | null) {
  const [state,setState] = useState<Operation | null>(null);
  const [error,setError] = useState('');
  const [refresh,setRefresh] = useState(0);
  useEffect(() => {
    if (!id) return;
    let stopped=false, stream: EventSource | undefined, timer: ReturnType<typeof setTimeout>;
    async function read() {
      try {
        const next=await request<Operation>('/api/operations/'+id);
        if (stopped) return;
        setState(next);setError('');
        if (next.status !== 'running') {stream?.close();return;}
        if (!stream && typeof EventSource !== 'undefined') {
          const after=next.events.at(-1)?.id ?? 0;
          stream=new EventSource('/api/operations/'+id+'/events?after='+after);
          stream.onmessage=event => {
            if (JSON.parse(event.data).kind !== 'workflow_events') void read();
          };
          stream.onerror=() => {stream?.close();stream=undefined;clearTimeout(timer);timer=setTimeout(read,2000);};
        } else if (!stream) timer=setTimeout(read,1000);
      } catch (e) {if (!stopped) {setError((e as Error).message);timer=setTimeout(read,3000);}}
    }
    void read();
    return () => {stopped=true;stream?.close();clearTimeout(timer);};
  },[id,refresh]);
  return {state,error,reload:()=>setRefresh(x=>x+1)};
}

export function PreparationPanel({id,upload,onReady,onWaiting}: {
  id: string | null; upload?: boolean; onReady: (materialId:string)=>Promise<void>; onWaiting?: ()=>void;
}) {
  const {state,error,reload}=useOperation(id);
  const opened=useRef('');
  const [notice,setNotice]=useState('');
  const [acting,setActing]=useState(false);
  async function act(path:string,body:object={}) {
    setActing(true);setNotice('');
    try {await request(path,body);reload();} catch(e) {setNotice((e as Error).message);} finally {setActing(false);}
  }
  useEffect(()=>{
    if(state && ['failed','interrupted','needs_clarification'].includes(state.status)) onWaiting?.();
    if (id && state?.status==='ready' && state.result?.id && opened.current!==id) {
      opened.current=id; void onReady(state.result.id);
    }
  },[state,id,onReady]);
  const descriptions = [...new Set(state?.events.map(event => preparationLabels[event.kind]).filter(Boolean))];
  if (state?.status === 'ready') descriptions.push({label: 'Learning map ready'});
  if (state?.status === 'needs_clarification') descriptions.push({label: 'Choose a subject'});
  if (!descriptions.length && (id || !upload)) descriptions.push({label: 'Preparing your learning map'});
  const steps = descriptions.map<FlowStep>((description, index) => ({
    ...description,
    id: description.label,
    state: index < descriptions.length - 1 || state?.status === 'ready' ? 'done'
      : !state || state.status === 'running' ? 'working' : 'paused',
  }));
  if (upload || state?.kind === 'upload') {
    steps.unshift({id: 'upload', label: id ? 'Files uploaded' : 'Uploading files', state: id ? 'done' : 'working'});
  }
  return <section className="preparation-panel" aria-label="Learning preparation">
    <ActivityFlow label="Preparation progress" steps={steps} />
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
  const stage = state?.events.filter(event => videoLabels[event.kind]).at(-1);
  const discarded = state?.result?.discarded;
  const paused = state?.status === 'failed' || state?.status === 'interrupted';
  return <section className="video-job" aria-label="Animation progress"><strong>{title}</strong>{state?.content && <p>{state.content}</p>}
    {discarded ? <p role="status">Animation discarded because your work changed.</p> : <>
      <ActivityFlow label="Animation step" steps={[{
        ...(stage ? videoLabels[stage.kind] : {label: 'Preparing your animation', actor: 'Video renderer'}),
        id: stage?.kind ?? 'preparing', state: paused ? 'paused' : 'working',
      }]} />
      {paused && <p role="status">Video unavailable. Your written explanation is saved.</p>}
    </>}
    {(error || state?.error || retryError) && <p>{error || state?.error || retryError}</p>}
    {paused && <button type="button" onClick={async()=>{
      try {const next=await request<{operation_id:string}>('/api/operations/'+currentId+'/retry-video',{});setCurrentId(next.operation_id);}
      catch(e){setRetryError((e as Error).message);}
    }}>Retry animation</button>}
  </section>;
}
