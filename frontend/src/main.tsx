import { useEffect, useRef, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { MotionConfig } from 'motion/react';
import Workspace, { annotationRange } from './Workspace';
import VisualPlayer, { FlowGraph } from './Visualization';
import type { Annotation, Assessment, HistoryItem, Observer, Result, Study, Tool } from './types';
import '../styles.css';

const storageKey = 'studyroom-auto-session';
const tools: Tool[] = ['hint', 'explanation', 'highlight', 'visualization', 'guiding_question'];
const label = (name: string) => name.replaceAll('_', ' ');

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => null);
  if (!response.ok) throw Error(typeof data?.detail === 'string' ? data.detail : 'Request failed (' + response.status + ').');
  return data as T;
}

function post<T>(path: string, data: object) {
  return api<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
}

type Message = Study | {
  type: 'intervention'; event_id: number; proactive: boolean; result: Result; revision: number;
} | { type: 'assessment'; result: Assessment }
  | { type: 'ack'; event_id: number; event_type: string; action: string; revision: number; content?: string }
  | ({ type: 'observer_status' } & Observer)
  | { type: 'busy'; active: boolean }
  | { type: 'session_summary'; result: { summary: string; next_goal: string } }
  | { type: 'error'; message: string };

function Intervention({ result }: { result: Result }) {
  const { metadata } = result;
  return <>
    <strong>{label(result.type)}</strong><p>{result.content}</p>
    {metadata.visualization && <VisualPlayer data={metadata.visualization} />}
    {!metadata.visualization && metadata.nodes && metadata.edges &&
      <><FlowGraph graph={{ nodes: metadata.nodes, edges: metadata.edges }} label="Teaching visualization" />
        <ol>{metadata.steps?.map((step, index) => <li key={index}>{step}</li>)}</ol></>}
  </>;
}

function App() {
  const [sid, setSid] = useState(() => Number(localStorage.getItem(storageKey)) || null);
  const [study, setStudy] = useState<Study | null>(null);
  const [observer, setObserver] = useState<Observer>({});
  const [notice, setNotice] = useState('');
  const [uploading, setUploading] = useState(false);
  const [connected, setConnected] = useState(false);
  const [pending, setPending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [answer, setAnswer] = useState('');
  const [chat, setChat] = useState('');
  const [assessment, setAssessment] = useState('');
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [summary, setSummary] = useState('');
  const ws = useRef<WebSocket | null>(null);
  const current = useRef<Study | null>(null);
  const workspace = useRef('');
  const sent = useRef('');
  const revision = useRef(0);
  const pendingEdits = useRef(0);
  const editTimer = useRef<number | undefined>(undefined);

  function send(event: object) {
    if (ws.current?.readyState !== WebSocket.OPEN) {
      setNotice('Connection interrupted. Reconnecting; your work remains here.');
      return false;
    }
    ws.current.send(JSON.stringify(event));
    return true;
  }

  function flushEdits() {
    window.clearTimeout(editTimer.current);
    if (workspace.current === sent.current) return true;
    if (!send({ type: 'student_action', action: 'edit', content: workspace.current })) return false;
    pendingEdits.current++;
    sent.current = workspace.current;
    return true;
  }

  function applyStudy(view: Study) {
    const changed = current.current?.state.current_problem.id !== view.state.current_problem.id
      || current.current?.material.id !== view.material.id;
    current.current = view;
    if (changed) {
      window.clearTimeout(editTimer.current);
      sent.current = view.state.workspace;
      workspace.current = sent.current.replace(/\r\n?/g, '\n');
      setAnswer('');
      setAnnotations([]);
    }
    setStudy(view);
    revision.current = Math.max(revision.current, view.state.observer_status.revision ?? 0);
    setObserver(view.state.observer_status);
    setPending(Boolean(view.state.current_problem.solved && !view.state.study_complete));
  }

  function appendHistory(item: HistoryItem) {
    setHistory(items => items.some(entry => entry.id === item.id) ? items : [...items, item]);
  }

  function onMessage(message: Message) {
    switch (message.type) {
      case 'study_state': applyStudy(message); break;
      case 'observer_status':
        revision.current = Math.max(revision.current, message.revision ?? 0);
        setObserver(message);
        break;
      case 'ack':
        revision.current = Math.max(revision.current, message.revision);
        if (message.action === 'edit') pendingEdits.current = Math.max(0, pendingEdits.current - 1);
        if (message.event_type === 'student_message' && message.content) {
          appendHistory({ id: message.event_id, type: 'student_message', data: { content: message.content } });
        }
        break;
      case 'intervention': {
        const stale = workspace.current !== sent.current || pendingEdits.current > 0 || message.revision < revision.current;
        if (message.proactive && stale) break;
        const { result } = message;
        if (result.type === 'highlight' && !stale) {
          const items = result.metadata.annotations ?? [];
          setAnnotations(items.every(item => annotationRange(workspace.current, item)) ? items : []);
        }
        appendHistory({ id: message.event_id, type: 'intervention',
          data: { ...result, proactive: message.proactive } });
        break;
      }
      case 'assessment': {
        const advances = message.result.correct && (message.result.verified || message.result.method === 'llm');
        setAssessment((message.result.correct ? 'Correct. ' : 'Not correct yet. ') + message.result.reason
          + (advances ? ' Preparing your next question.' : ''));
        setPending(advances);
        break;
      }
      case 'busy': setBusy(message.active); break;
      case 'error':
        pendingEdits.current = 0;
        setNotice(message.message); setPending(false); setBusy(false);
        break;
      case 'session_summary':
        setSummary(message.result.summary + ' ' + message.result.next_goal);
        setPending(false);
        setObserver({ decision: 'INACTIVE', reason: 'Session ended' });
        if (current.current) applyStudy({ ...current.current, state: { ...current.current.state, ended: true,
          observer_status: { decision: 'INACTIVE', reason: 'Session ended' } } });
        break;
    }
  }

  useEffect(() => {
    if (!sid || uploading) return;
    let stopped = false;
    let reconnect: number;
    let opened = false;
    const path = '/api/sessions/' + sid;
    async function restore() {
      const view = await api<Study>(path + '/study');
      return view.state.current_problem.solved && !view.state.study_complete && !view.state.ended
        ? post<Study>(path + '/study', { material_id: view.state.material_id }) : view;
    }
    function connect() {
      pendingEdits.current = 0;
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      const socket = new WebSocket(protocol + '//' + location.host + '/ws/' + sid);
      ws.current = socket;
      socket.onmessage = event => { if (!stopped && ws.current === socket) onMessage(JSON.parse(event.data)); };
      socket.onopen = async () => {
        if (stopped || ws.current !== socket) return;
        try {
          const view = await restore();
          if (stopped || ws.current !== socket || socket.readyState !== WebSocket.OPEN) return;
          applyStudy(view);
          if (opened && view.state.workspace !== workspace.current) sent.current = view.state.workspace;
          opened = true;
          flushEdits();
          setConnected(true);
          setNotice('');
        } catch (error) {
          if (!stopped) setNotice((error as Error).message);
        }
      };
      socket.onclose = () => {
        if (stopped || ws.current !== socket) return;
        setConnected(false);
        setBusy(false);
        reconnect = window.setTimeout(connect, 2000);
      };
    }
    void api<{ history: HistoryItem[] }>(path).then(data => {
      if (stopped) return;
      const saved = data.history.filter(item => item.type === 'student_message' || item.type === 'intervention');
      setHistory(items => [...saved, ...items.filter(item => !saved.some(old => old.id === item.id))]);
      connect();
    }).catch(error => {
      if (!stopped) setNotice((error as Error).message);
    });
    const idle = window.setInterval(() => {
      if (!document.hidden && current.current?.state.current_problem.id
          && !current.current.state.study_complete && !current.current.state.ended
          && ws.current?.readyState === WebSocket.OPEN) send({ type: 'student_action', action: 'idle' });
    }, 20000);
    return () => {
      stopped = true;
      window.clearInterval(idle);
      window.clearTimeout(reconnect);
      window.clearTimeout(editTimer.current);
      ws.current?.close();
      ws.current = null;
    };
  }, [sid, uploading]);

  async function upload(file: File) {
    setUploading(true);
    setConnected(false);
    setNotice('');
    try {
      const form = new FormData();
      form.append('file', file);
      const material = await api<{ id: string }>('/api/materials', { method: 'POST', body: form });
      const session = await api<{ id: number }>('/api/sessions', { method: 'POST' });
      const next = await post<Study>('/api/sessions/' + session.id + '/study', { material_id: material.id });
      setHistory([]);
      setChat(''); setSummary(''); setAssessment('');
      revision.current = 0;
      localStorage.setItem(storageKey, String(session.id));
      applyStudy(next);
      setSid(session.id);
    } catch (error) {
      setNotice((error as Error).message);
    } finally {
      setUploading(false);
    }
  }

  function ask(content: string) {
    if (disabled || busy || !content.trim() || !flushEdits()) return;
    if (send({ type: 'student_message', content: content.trim() })) {
      setBusy(true);
      setChat('');
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!answer.trim() || disabled || !flushEdits()) return;
    if (send({ type: 'problem_attempt', content: answer, problem_id: problem?.id })) setPending(true);
  }

  async function skip() {
    if (!flushEdits()) return;
    setPending(true);
    try {
      applyStudy(await post<Study>('/api/sessions/' + sid + '/skip', { problem_id: problem?.id }));
      setAssessment('Question skipped. Mastery unchanged.');
    } catch (error) {
      setNotice((error as Error).message);
    } finally { setPending(false); }
  }

  const problem = study?.state.current_problem;
  const inactive = Boolean(study?.state.ended || study?.state.study_complete);
  const disabled = uploading || !connected || !problem?.id || inactive || pending;
  const activeNode = study?.graph.nodes.find(node => node.active);
  const mastery = activeNode?.mastery;

  return <MotionConfig reducedMotion="user">
    <header><strong>Studyroom</strong><span id="status" role="status">
      {uploading ? 'Preparing your study material…' : study?.state.study_complete ? 'Learning target reached'
        : study?.state.ended ? 'Session ended' : study && !connected ? 'Connecting…'
        : study?.material.name ?? 'Upload material to begin'}
    </span><label className="upload">Upload material<input id="upload" type="file" accept=".pdf,.txt,.md"
      disabled={uploading} onChange={event => {
        const file = event.target.files?.[0];
        event.target.value = '';
        if (file) void upload(file);
      }} /></label></header>
    {notice && <p id="notice" role="alert">{notice}</p>}
    <main>
      <section className="panel question-panel">
        <p className="eyebrow">CURRENT TOPIC</p>
        <h1 id="concept">{study?.state.study_complete ? 'Material complete' : study?.state.current_concept ?? 'Your next learning step'}</h1>
        <div className="mastery"><span id="mastery">Estimated mastery: {mastery == null ? 'Not assessed yet' : Math.round(mastery * 100) + '%'}</span>
          {mastery != null && <progress max={1} value={mastery} aria-label="Estimated mastery" />}</div>
        <p className="muted">An estimate from your answers.</p>
        <p id="problem" className="problem">{study?.state.study_complete ? 'Every concept has reached the current progression target.'
          : problem?.prompt ?? 'Upload lecture notes to build a concept graph and get your first question.'}</p>
        <div id="observer-status" role="status">Observer: {observer.decision ?? 'Waiting'} · {observer.reason ?? 'Waiting for activity'}</div>
        <details><summary>Observer details</summary><pre>{JSON.stringify(observer, null, 2)}</pre></details>
        <h3>Your reasoning</h3>
        <Workspace key={problem?.id ?? 'empty'} initial={study?.state.workspace ?? ''} disabled={uploading || !problem?.id || inactive}
          annotations={annotations} canAsk={!disabled && !busy}
          onAsk={item => ask('Please explain your annotation on line '
            + item.line + ' about “' + item.quote + '”: ' + item.reason)}
          onDismiss={item => setAnnotations(items => items.filter(candidate => candidate !== item))}
          onEdit={text => {
            workspace.current = text;
            setAnnotations([]);
            window.clearTimeout(editTimer.current);
            editTimer.current = window.setTimeout(flushEdits, 1000);
          }} />
        <form onSubmit={submit} id="answer-form">
          <label htmlFor="answer">Your final answer</label>
          <div className="answer-row"><input id="answer" maxLength={20000} value={answer} onChange={event => setAnswer(event.target.value)} disabled={disabled} />
            <button id="submit" disabled={disabled}>Submit answer</button></div>
        </form>
        <div id="manual-tools">{tools.map(tool => <button type="button" key={tool} data-tool={tool} disabled={disabled || busy}
          onClick={() => {
            if (flushEdits() && send({ type: 'student_action', action: 'hint_request', tool })) setBusy(true);
          }}>{label(tool)}</button>)}
          <button id="skip" type="button" disabled={disabled || busy} onClick={skip}>Skip question</button></div>
        <p id="assessment" aria-live="polite">{assessment}</p>
        <section className="chat" aria-label="Tutor chat">
          <h2>Tutor chat</h2>
          <form onSubmit={event => { event.preventDefault(); ask(chat); }}>
            <label htmlFor="chat">Ask your tutor</label>
            <div className="answer-row"><input id="chat" maxLength={20000} value={chat} onChange={event => setChat(event.target.value)} disabled={disabled} />
              <button disabled={disabled || busy || !chat.trim()}>Send</button></div>
          </form>
          <div id="interventions" role="log" aria-live="polite">
            {history.map(item => item.type === 'student_message'
              ? <article className="student-message" key={item.id}><strong>You</strong><p>{item.data.content}</p></article>
              : <article className="intervention" key={item.id}>
                <Intervention result={item.data} />
                {observer.feedback_intervention_id === item.id && !inactive &&
                  <div className="player-controls">{([1, -1] as const).map(rating =>
                    <button type="button" key={rating} disabled={!connected || busy} onClick={() => {
                      if (send({ type: 'feedback', intervention_id: item.id, rating })) setObserver(status =>
                        ({ ...status, feedback_intervention_id: null }));
                    }}>{rating === 1 ? 'Helpful' : 'Not helpful'}</button>)}</div>}
              </article>)}
          </div>
          {busy && <p id="busy" role="status">Your tutor is working…</p>}
        </section>
        {study && !study.state.ended && <button type="button" disabled={!connected || pending || busy || uploading}
          onClick={() => { if (flushEdits() && send({ type: 'session_end' })) setPending(true); }}>End session</button>}
        {summary && <p role="status">{summary}</p>}
      </section>
      <aside className="panel graph-panel">
        <p className="eyebrow">YOUR LEARNING MAP</p><h2>Concept graph</h2>
        <p className="muted">Arrows point from a prerequisite to the next concept.</p>
        <div id="graph">{study ? <FlowGraph key={study.material.id} graph={study.graph}
          masteryTarget={study.mastery_target} label="Concept graph and estimated mastery" />
          : <p className="muted">Your map will appear after processing.</p>}</div>
        {study && <details><summary id="material-name">{study.material.name}</summary>
          <pre>{study.material.chunks[0]?.text}</pre></details>}
      </aside>
    </main>
  </MotionConfig>;
}

createRoot(document.getElementById('root')!).render(<App />);

