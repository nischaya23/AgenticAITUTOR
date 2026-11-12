import { useEffect, useRef, useState, type ReactNode } from 'react';

export type WorkflowEvent = { id: string; cursor: number; run_id: string; span_id?: string;
  type: string; category: string; status: string; timestamp: string; data: Record<string, unknown> };
type Run = { id: string; name: string; status: string; cursor: number; started_at: string; operation_id?: string;
  trigger: Record<string, unknown> };
type Span = { id: string; name: string; status: string; category: string; parent_id?: string;
  dependencies: string[]; consumed: string[]; produced: string[]; data: Record<string, unknown> };
type Detail = Run & { spans: Span[]; events: WorkflowEvent[];
  edges: { source: string; target: string; kind: string; source_run_id?: string; label?: string }[]; artifacts: { id: string; kind: string }[] };
type Page = { runs: Run[]; active_runs: Run[]; next_cursor?: number; watermark: number; tracking: string };
type Replay = { events: WorkflowEvent[]; cursor: number; has_more: boolean; watermark: number };
const active = (status: string) => ['queued','running','waiting'].includes(status);
const EMPTY_EVENTS: WorkflowEvent[] = [];
const filters = ['All', 'Agents', 'Tools', 'Memory', 'Context', 'Errors'];
export const statusLabel = (status: string) => status === 'warnings' ? 'Completed with warnings' : status.charAt(0).toUpperCase()+status.slice(1);
const words = (value: string) => value.replaceAll('_', ' ').replaceAll('.', ' · ');
async function read<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { signal });
  if (!response.ok) throw Error('Activity tracking is incomplete. Tutoring can continue.');
  return response.json();
}

export function mergeEvents(old: WorkflowEvent[], incoming: WorkflowEvent[]) {
  return [...new Map([...old, ...incoming].map(e => [e.id, e])).values()].sort((a,b) => a.cursor-b.cursor);
}

// Only repeated reads/unchanged numerical decisions are collapsed. All evidence survives.
export function groupedEvents(events: WorkflowEvent[]) {
  const result: { key: string; items: WorkflowEvent[] }[] = [];
  for (const event of events) {
    const groupable = event.type === 'memory.read' || event.type === 'observer.numerical';
    const signature = event.type+'|'+(event.type === 'memory.read'
      ? event.data.message : event.data.decision)+'|'+String(event.data.memory_revision ?? event.data.revision ?? '')+'|'+JSON.stringify(event.data.source_ids ?? []);
    const found = groupable ? result.find(g => g.key === signature) : undefined;
    if (found) found.items.push(event); else result.push({key: groupable ? signature : event.id, items: [event]});
  }
  return result;
}

function PublicValue({value}: {value: unknown}) {
  if (value === null || value === undefined) return <span>None</span>;
  if (typeof value === 'string') {
    if (/^https?:\/\//.test(value)) return <a href={value} target="_blank" rel="noopener noreferrer">{value}</a>;
    return <span className="workflow-value">{value}</span>;
  }
  if (Array.isArray(value)) return <ol>{value.map((item, i) => <li key={i}><PublicValue value={item} /></li>)}</ol>;
  if (typeof value === 'object') return <dl>{Object.entries(value as Record<string,unknown>).map(([key,item]) =>
    <div key={key}><dt>{words(key)}</dt><dd><PublicValue value={item} /></dd></div>)}</dl>;
  return <span>{String(value)}</span>;
}

function Artifact({id,kind}: {id:string;kind:string}) {
  const [value,setValue] = useState<unknown>();
  const [error,setError] = useState('');
  return <details onToggle={event => {
    if (event.currentTarget.open && value === undefined) void read<{data:unknown}>('/api/workflow/artifacts/'+id)
      .then(data => {setValue(data.data);setError('');}).catch(e => setError(e.message));
  }}><summary>{words(kind)}</summary>{error ? <p role="alert">{error}</p> : value === undefined ? <p>Loading evidence…</p> : <PublicValue value={value} />}</details>;
}

function groupRuns(runs: Detail[]) {
  const groups: Detail[][] = [];
  const key = (run: Detail) => {
    const event = run.events.find(e => e.type === 'observer.numerical');
    return run.name === 'Observer · Numerical decision' && !active(run.status) && event
      ? String(event.data.decision)+'|'+String((event.data.status_details as Record<string,unknown>)?.reason) : null;
  };
  for (const run of runs) {
    const previous = groups.at(-1);
    if (previous && key(run) !== null && key(previous[0]) === key(run)) previous.push(run);
    else groups.push([run]);
  }
  return groups;
}

function DependencyGraph({run,selected,onSelect}: {run:Detail;selected:string | null;onSelect:(id:string)=>void}) {
  const external = [...new Map(run.edges.filter(e => !run.spans.some(s => s.id === e.source)).map(e => [e.source,{id:e.source,name:'Consumed '+(e.label ?? 'earlier run'),status:'completed'}])).values()];
  const nodes = [...external,...run.spans];
  const width = Math.max(480,nodes.length*190+40);
  const positions = new Map(nodes.map((s,i) => [s.id,{x:20+i*190,y:35+(i%2)*90}]));
  return <div className="workflow-graph"><svg width={width} height="220" aria-label="Agent and tool dependencies" role="group">
    <defs><marker id={'arrow-'+run.id} viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" /></marker></defs>
    {run.edges.map((edge,i) => {
      const from = positions.get(edge.source), to = positions.get(edge.target);
      return from && to ? <path key={i} d={`M ${from.x+165} ${from.y+24} C ${from.x+182} ${from.y+24}, ${to.x-16} ${to.y+24}, ${to.x} ${to.y+24}`}
        fill="none" stroke="currentColor" opacity={edge.kind === 'parent' ? .3 : .8} strokeDasharray={edge.kind === 'parent' ? '4 3' : undefined} markerEnd={'url(#arrow-'+run.id+')'}><title>{edge.kind}: {edge.source} → {edge.target}</title></path> : null;
    })}
    {nodes.map(node => {
      const pos = positions.get(node.id)!;
      return <g key={node.id} role="button" tabIndex={0} aria-label={node.name+' · '+statusLabel(node.status)} aria-pressed={selected === node.id}
        onClick={() => onSelect(node.id)} onKeyDown={event => {if (event.key === 'Enter' || event.key === ' ') {event.preventDefault();onSelect(node.id);}}}>
        <title>{node.name} · {statusLabel(node.status)}</title>
        <rect x={pos.x} y={pos.y} width="165" height="50" rx="7" fill="var(--surface)" stroke="currentColor" strokeWidth={selected === node.id ? 3 : 1} />
        <text x={pos.x+8} y={pos.y+20} fill="currentColor" fontSize="11">{node.name.length > 24 ? node.name.slice(0,23)+'…' : node.name}</text>
        <text x={pos.x+8} y={pos.y+38} fill="currentColor" fontSize="10">{statusLabel(node.status)}</text>
      </g>;
    })}
  </svg></div>;
}

function RunCard({run,filter,renderMessage}: {run: Detail; filter: string;renderMessage?:(id:number)=>ReactNode}) {
  const [selected,setSelected] = useState<string | null>(null);
  const [expanded,setExpanded] = useState(active(run.status));
  const selectedSpan = run.spans.find(s => s.id === selected);
  const selectedEvent = run.events.find(e => e.id === selected) ?? run.events.find(e => e.type === 'context.dependency' && (e.data.source_span_id ?? e.data.source_run_id) === selected);
  const events = run.events.filter(e => filter === 'All' || e.category === filter.toLowerCase());
  const visible = events.filter(e => !e.type.startsWith('artifact.') && !e.type.startsWith('application.'));
  const outputs = run.events.filter(e => ['application.intervention','application.student_message'].includes(e.type));
  const aids = selectedSpan ? [...selectedSpan.consumed, ...selectedSpan.produced] : selectedEvent?.data.artifact_id
    ? [String(selectedEvent.data.artifact_id)] : [];
  const summary = run.spans.filter(s => active(s.status) && !run.spans.some(child => child.parent_id === s.id && active(child.status)));
  function selectNode(id: string) {
    setSelected(id);
    const event = run.events.find(e => e.type === 'context.dependency' && (e.data.source_span_id ?? e.data.source_run_id) === id)
      ?? run.events.find(e => e.span_id === id && e.type === 'span.started')
      ?? run.events.find(e => e.span_id === id);
    const stepId = event?.type === 'span.started' ? id : event?.id ?? id;
    document.getElementById('workflow-step-'+stepId)?.scrollIntoView?.({block:'nearest', behavior:'smooth'});
  }
  return <article className={'workflow-run workflow-status-'+run.status} data-run-id={run.id}>
    <details open={expanded || filter !== 'All'} onToggle={event => setExpanded(event.currentTarget.open)}>
      <summary><strong>{run.name}</strong> <span className="workflow-state">{statusLabel(run.status)}</span>
        {summary.length > 0 && <span> · {summary.length} tasks active</span>}</summary>
      <time dateTime={run.started_at}>{new Date(run.started_at).toLocaleString()}</time>
      {Object.keys(run.trigger ?? {}).length > 0 && <details><summary>Trigger and snapshot</summary><PublicValue value={run.trigger} /></details>}
      <DependencyGraph run={run} selected={selected} onSelect={selectNode} />
      <div className="workflow-graph-list" role="group" aria-label={'Dependency graph for '+run.name}>
        {run.spans.map((node, index) => <div key={node.id} className="workflow-graph-node">
          <button type="button" aria-pressed={selected === node.id} onClick={() => selectNode(node.id)}>
            {index+1}. {node.name} · {statusLabel(node.status)}</button>
          <small>{run.edges.filter(edge => edge.target === node.id).map(edge => {
            const source = run.spans.find(s => s.id === edge.source);
            return source ? (edge.kind === 'parent' ? 'Within ' : 'After ')+source.name : 'Consumes earlier stage '+edge.source.slice(0,8);
          }).join(' · ') || 'Independent branch'}</small>
        </div>)}
      </div>
      <ol className="workflow-steps">{groupedEvents(visible).map(group => {
        const event = group.items[0];
        const title = String(event.data.message ?? event.data.name ?? words(event.type));
        const chosen = selected === event.id || Boolean(event.span_id && selected === event.span_id);
        return <li key={group.key} id={'workflow-step-'+(event.type === 'span.started' ? event.span_id : event.id)} className={chosen ? 'workflow-selected' : ''}>
          <button type="button" aria-pressed={chosen} onClick={() => setSelected(event.type === 'context.received' ? event.span_id ?? event.id : event.id)}>
            {title} {event.data.decision ? ' · '+event.data.decision : ''} <small>{statusLabel(event.status)}</small></button>
          {event.data.query ? <p>{String(event.data.query)}</p> : null}
          {event.data.url ? <p><a href={String(event.data.url)} target="_blank" rel="noopener noreferrer">{String(event.data.title ?? event.data.url)}</a></p> : null}
          {event.data.reason ? <p>{String(event.data.reason)}</p> : null}
          {group.items.length > 1 && <details><summary>{group.items.length} recorded steps · {new Date(event.timestamp).toLocaleTimeString()}–{new Date(group.items.at(-1)!.timestamp).toLocaleTimeString()}</summary>
            {group.items.map(item => <details key={item.id}><summary>{new Date(item.timestamp).toLocaleTimeString()}</summary><PublicValue value={item.data} />{item.data.artifact_id ? <Artifact id={String(item.data.artifact_id)} kind="Recorded evidence" /> : null}</details>)}</details>}
        </li>;
      })}</ol>
      {selected && <aside className="workflow-inspector" aria-label="Activity inspector">
        <button type="button" onClick={() => setSelected(null)}>Close inspector</button>
        <h4>{selectedSpan?.name ?? words(selectedEvent?.type ?? '')}</h4>
        <PublicValue value={selectedSpan?.data ?? selectedEvent?.data} />
        {selectedSpan && <p>Consumes {selectedSpan.consumed.length} artifacts · Produces {selectedSpan.produced.length} artifacts</p>}
        {[...new Set(aids)].map(id => <Artifact key={id} id={id} kind={run.artifacts.find(a => a.id === id)?.kind ?? 'Evidence'} />)}
        {selectedSpan && run.events.filter(e => e.span_id === selectedSpan.id && e.data.artifact_id).map(e =>
          !aids.includes(String(e.data.artifact_id)) && <Artifact key={e.id} id={String(e.data.artifact_id)} kind={String(e.data.kind ?? 'Evidence')} />)}
      </aside>}
      <details><summary>All recorded public artifacts ({run.artifacts.length})</summary>
        {run.artifacts.map(a => <Artifact key={a.id} {...a} />)}</details>
    </details>
    {filter === 'All' && outputs.map(e => <div key={e.id}>{renderMessage?.(Number(e.data.event_id))}</div>)}
  </article>;
}

export default function Workflow({sessionId,operationId,connected=true,trackingIncomplete=false,batch=EMPTY_EVENTS,refreshKey=0,messageIds=[],messageRuns={},renderMessage,empty,extras}: {
  sessionId?:number;operationId?:string;connected?:boolean;trackingIncomplete?:boolean;batch?:WorkflowEvent[];refreshKey?:number;
  messageIds?:number[];messageRuns?:Record<number,string | null | undefined>;renderMessage?:(id:number)=>ReactNode;empty?:ReactNode;extras?:ReactNode;
}) {
  const [runs,setRuns] = useState<Record<string,Detail>>({});
  const [next,setNext] = useState<number | undefined>();
  const [error,setError] = useState('');
  const [filter,setFilter] = useState('All');
  const [newActivity,setNewActivity] = useState(false);
  const [loading,setLoading] = useState(true);
  const [retry,setRetry] = useState(0);
  const scroll = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const cursor = useRef(0);
  const loaded = useRef(false);
  const requestVersion = useRef(0);
  const pendingScroll = useRef<{height:number;top:number} | null>(null);
  const base = operationId ? '/api/operations/'+operationId+'/workflow' : '/api/sessions/'+sessionId+'/workflow';

  useEffect(() => {
    const abort = new AbortController();
    const version = ++requestVersion.current;
    async function sync() {
      try {
        const page = await read<Page>(base, abort.signal);
        const changed = new Set<string>([...page.runs,...page.active_runs].map(r => r.id));
        let after = loaded.current ? cursor.current : page.watermark;
        if (!operationId) {
          let replay: Replay;
          do {
            replay = await read<Replay>(base+'/events?after='+after, abort.signal);
            replay.events.forEach(e => changed.add(e.run_id));
            after = replay.cursor;
          } while (replay.has_more);
        }
        batch.forEach(e => changed.add(e.run_id));
        const values = await Promise.all([...changed].map(id => read<Detail>('/api/workflow/runs/'+id, abort.signal)));
        if (version !== requestVersion.current) return;
        const incoming = Object.fromEntries(values.map(run => [run.id,run]));
        setRuns(old => ({...old,...incoming}));
        if (!loaded.current) setNext(page.next_cursor);
        cursor.current = after;
        loaded.current = true;
        setError(page.tracking === 'incomplete' ? 'Activity tracking is incomplete. Tutoring can continue.' : '');
        setLoading(false);
        if (!atBottom.current && (batch.length || refreshKey)) setNewActivity(true);
      } catch (e) {if (!abort.signal.aborted) {setError((e as Error).message);setLoading(false);}}
    }
    void sync();
    return () => abort.abort();
  },[base,connected,batch,refreshKey,retry]);

  useEffect(() => {
    const element = scroll.current;
    if (!element) return;
    if (pendingScroll.current) {
      element.scrollTop = pendingScroll.current.top + element.scrollHeight-pendingScroll.current.height;
      pendingScroll.current = null;
    } else if (atBottom.current) element.scrollTop = element.scrollHeight;
    else setNewActivity(true);
  },[runs,messageIds.length,extras]);

  async function older() {
    try {
      const page = await read<Page>(base+'?before='+next);
      const values = await Promise.all([...page.runs,...page.active_runs].map(r => read<Detail>('/api/workflow/runs/'+r.id)));
      if (scroll.current) pendingScroll.current = {height:scroll.current.scrollHeight,top:scroll.current.scrollTop};
      setRuns(old => ({...old,...Object.fromEntries(values.map(r => [r.id,r]))}));
      setNext(page.next_cursor);
    } catch(e) {setError((e as Error).message);}
  }
  const sorted = Object.values(runs).sort((a,b) => (a.events[0]?.cursor ?? 0)-(b.events[0]?.cursor ?? 0));
  const running = sorted.filter(r => active(r.status));
  const ownedMessages = new Set(sorted.flatMap(r => r.events.filter(e => ['application.intervention','application.student_message'].includes(e.type)).map(e => Number(e.data.event_id))));
  const archived = messageIds.filter(id => !ownedMessages.has(id));
  const filtered = sorted.filter(run => filter === 'All' || run.events.some(e => e.category === filter.toLowerCase()));
  return <div className="workflow">
    <div className="workflow-header" role="status">
      <strong>{running.length} active runs</strong> · {running.filter(r => r.status === 'queued').length} queued
      {running.map(r => <span className="workflow-active" key={r.id}>{r.name} · {statusLabel(r.status)}
        {r.spans.filter(s => active(s.status) && !r.spans.some(child => child.parent_id === s.id && active(child.status))).map(s => <small key={s.id}>{s.name}{s.data.status_label ? ' · '+s.data.status_label : ''}</small>)}</span>)}
    </div>
    <nav className="workflow-filters" aria-label="Activity filters">{filters.map(name => <button key={name} type="button"
      aria-pressed={filter === name} onClick={() => setFilter(name)}>{name}</button>)}</nav>
    {(error || trackingIncomplete) && <p role="alert">{error || 'Activity tracking is incomplete. Tutoring can continue.'} <button type="button" onClick={() => setRetry(v => v+1)}>Retry tracking</button></p>}
    <div className="workflow-scroll" id={sessionId ? 'interventions' : undefined} ref={scroll} role="log" aria-live="polite"
      onScroll={() => {const el = scroll.current!; atBottom.current = el.scrollHeight-el.scrollTop-el.clientHeight < 48;
        if (atBottom.current) setNewActivity(false);}}>
      {next && <button type="button" onClick={() => void older()}>Load earlier activity</button>}
      {loading && <p>Loading activity…</p>}
      {filter === 'All' && archived.map(id => <article className="workflow-legacy" key={id}>
        {messageRuns[id] ? <button type="button" onClick={() => void read<Detail>('/api/workflow/runs/'+messageRuns[id]).then(run => setRuns(old => ({...old,[run.id]:run}))).catch(e => setError(e.message))}>Load earlier workflow details</button> : <small>Detailed activity was not recorded.</small>}{renderMessage?.(id)}</article>)}
      {groupRuns(filtered).map(group => group.length === 1 ? <RunCard key={group[0].id} run={group[0]} filter={filter} renderMessage={renderMessage} /> :
        <details key={group[0].id} className="workflow-group"><summary>Observer · {String(group[0].events.find(e => e.type === 'observer.numerical')?.data.decision)} · {group.length} recorded decisions · {new Date(group[0].started_at).toLocaleTimeString()}–{new Date(group.at(-1)!.started_at).toLocaleTimeString()}</summary>
          {group.map(run => <RunCard key={run.id} run={run} filter={filter} renderMessage={renderMessage} />)}</details>)}
      {filter === 'All' && empty}{filter === 'All' && extras}
    </div>
    {newActivity && <button className="workflow-new" type="button" onClick={() => {
      if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
      atBottom.current = true; setNewActivity(false);
    }}>New activity ↓</button>}
  </div>;
}
