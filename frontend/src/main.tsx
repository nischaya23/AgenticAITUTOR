import { useEffect, useRef, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { MotionConfig } from 'motion/react';
import Auth, { authExpired, type User } from './Auth';
import LearningPlanPanel from './LearningPlan';
import DiagramView, { emptyDiagram } from './Diagram';
import Hypotheses from './Hypotheses';
import Workspace, { annotationRange } from './Workspace';
import VisualPlayer, { FlowGraph } from './Visualization';
import type { Annotation, Assessment, Diagram, DiagramAnnotation, HypothesisState, Response as StudentResponse, HistoryItem, Observer, PlanState, Result, Study, Tool } from './types';
import '../styles.css';

const tools: Tool[] = ['hint', 'explanation', 'highlight', 'visualization', 'guiding_question'];
const label = (name: string) => name.replaceAll('_', ' ');

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => null);
  if (response.status === 401) window.dispatchEvent(new Event(authExpired));
  if (!response.ok) throw Error(typeof data?.detail === 'string' ? data.detail : 'Request failed (' + response.status + ').');
  return data as T;
}

function post<T>(path: string, data: object) {
  return api<T>(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
}

type Message = Study | PlanState | {
  plan?: PlanState['plan']; plan_version?: number; plan_suggestion?: PlanState['plan_suggestion'];
  type: 'intervention'; event_id: number; proactive: boolean; result: Result; revision: number;
} | { type: 'assessment'; result: Assessment; stages?: { reflection: string; hypotheses: string } }
  | { type: 'hypotheses'; state: HypothesisState }
  | { type: 'memory_activity'; items: { message: string }[] }
  | { type: 'ack'; event_id: number; event_type: string; action: string; revision: number; response_revision?: number; content?: string }
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

function App({ user, logout, initialSession, onCreated, onUploadChange, onSessionsChange }: {
  user: User; logout: () => Promise<void>; initialSession: number | null;
  onCreated: (id: number) => void; onUploadChange: (uploading: boolean) => void; onSessionsChange: () => void;
}) {
  const draftPrefix = 'studyroom-draft-' + user.id + '-';
  const [sid, setSid] = useState<number | null>(initialSession);
  const [study, setStudy] = useState<Study | null>(null);
  const [observer, setObserver] = useState<Observer>({});
  const [notice, setNotice] = useState('');
  const [uploading, setUploading] = useState(false);
  const [topic, setTopic] = useState('');
  const [progressMessage, setProgressMessage] = useState('');
  const [connected, setConnected] = useState(false);
  const [pending, setPending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [diagramValue, setDiagramValue] = useState<Diagram>(emptyDiagram);
  const [diagramAnnotations, setDiagramAnnotations] = useState<DiagramAnnotation[]>([]);
  const [chat, setChat] = useState('');
  const [assessment, setAssessment] = useState('');
  const [memoryActivity, setMemoryActivity] = useState('');
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [summary, setSummary] = useState('');
  const ws = useRef<WebSocket | null>(null);
  const current = useRef<Study | null>(null);
  const workspace = useRef('');
  const diagram = useRef<Diagram>(emptyDiagram());
  const sent = useRef('');
  const revision = useRef(0);
  const responseRevision = useRef(0);
  const pendingEdits = useRef(0);
  const editTimer = useRef<number | undefined>(undefined);
  const uploadInput = useRef<HTMLInputElement>(null);
  const conversation = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const element = conversation.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [history.length, busy, study?.material.id]);

  function send(event: object) {
    if (ws.current?.readyState !== WebSocket.OPEN) {
      setNotice('Connection interrupted. Reconnecting; your work remains here.');
      return false;
    }
    ws.current.send(JSON.stringify(event));
    return true;
  }

  function responseValue(): StudentResponse {
    return { text: workspace.current, diagram: diagram.current };
  }

  function markDraft(status: 'stale' | 'analyzing' = 'stale') {
    if (!current.current) return;
    const panel: HypothesisState = { ...current.current.state.hypothesis_state,
      items: current.current.state.hypothesis_state?.items ?? [], status };
    const next = { ...current.current, state: { ...current.current.state, hypothesis_state: panel } };
    current.current = next; setStudy(next);
  }

  function saveDraft() {
    try { localStorage.setItem(draftPrefix + sid, JSON.stringify({ questionId: current.current?.state.current_problem.id, response: responseValue() })); }
    catch { /* The server still saves drafts if browser storage is unavailable. */ }
    markDraft();
    window.clearTimeout(editTimer.current);
    editTimer.current = window.setTimeout(flushEdits, 1000);
  }

  function flushEdits() {
    window.clearTimeout(editTimer.current);
    const response = responseValue();
    if (JSON.stringify(response) === sent.current) return true;
    if (!send({ type: 'student_action', action: 'edit', response, content: response.text })) return false;
    pendingEdits.current++;
    sent.current = JSON.stringify(response);
    return true;
  }

  function applyStudy(view: Study, sessionId = sid) {
    const previous = current.current;
    const serverResponse = view.state.response ?? { text: view.state.workspace,
      diagram: view.state.current_problem.starter_diagram ?? emptyDiagram() };
    if(previous?.material.id === view.material.id && (previous.state.plan_version ?? 0) > (view.state.plan_version ?? 0)){
      view = { ...view, state: { ...view.state, plan: previous.state.plan, plan_version: previous.state.plan_version, plan_suggestion: previous.state.plan_suggestion } };
    }
    const changed = current.current?.state.current_problem.id !== view.state.current_problem.id
      || current.current?.material.id !== view.material.id;
    current.current = view;
    if (changed) {
      window.clearTimeout(editTimer.current);
      const saved = serverResponse;
      sent.current = JSON.stringify(saved);
      let restored = saved;
      try {
        const local = JSON.parse(localStorage.getItem(draftPrefix + sessionId) ?? 'null');
        if (local?.questionId === view.state.current_problem.id && typeof local.response?.text === 'string'
          && Array.isArray(local.response?.diagram?.nodes) && Array.isArray(local.response?.diagram?.edges)) restored = local.response;
      } catch { /* A malformed local draft cannot replace the server copy. */ }
      workspace.current = restored.text.replace(/\r\n?/g, '\n');
      diagram.current = restored.diagram;
      setDiagramValue(restored.diagram);
      view = { ...view, state: { ...view.state, response: restored, workspace: restored.text } };
      setDiagramAnnotations([]);
      setAnnotations([]);
    }
    if (JSON.stringify(responseValue()) !== JSON.stringify(serverResponse) && view.state.hypothesis_state) {
      view = { ...view, state: { ...view.state, hypothesis_state: { ...view.state.hypothesis_state, status: 'stale' } } };
    }
    current.current = view;
    setStudy(view);
    if (view.state.last_assessment) setAssessment((view.state.last_assessment.correct ? 'Correct. ' : 'Not correct yet. ') + view.state.last_assessment.reason);
    revision.current = Math.max(revision.current, view.state.observer_status.revision ?? 0);
    responseRevision.current = Math.max(responseRevision.current, view.state.response_revision ?? 0);
    setObserver(view.state.observer_status);
    setPending(Boolean(view.state.current_problem.solved && !view.state.study_complete && !view.state.question_generation_error));
  }

  function appendHistory(item: HistoryItem) {
    setHistory(items => items.some(entry => entry.id === item.id) ? items : [...items, item]);
  }

  function applyPlan(value: PlanState) {
    const active = current.current;
    if (!active || value.plan_version < (active.state.plan_version ?? 0)) return;
    const next = { ...active, state: { ...active.state, plan: value.plan, plan_version: value.plan_version, plan_suggestion: value.plan_suggestion,
      hypothesis_state: active.state.hypothesis_state && value.plan_version !== active.state.plan_version
        ? { ...active.state.hypothesis_state, status: 'stale' as const } : active.state.hypothesis_state } };
    current.current = next; setStudy(next);
  }

  function onMessage(message: Message) {
    switch (message.type) {
      case 'memory_activity': setMemoryActivity(message.items.map(item => item.message).join(' ')); break;
      case 'plan': applyPlan(message); break;
      case 'hypotheses':
        if (current.current && message.state.question_id === current.current.state.current_problem.id
          && message.state.plan_version === (current.current.state.plan_version ?? 0)) {
          const panel = JSON.stringify(responseValue()) !== sent.current || pendingEdits.current > 0 || (message.state.response_revision ?? 0) < responseRevision.current
            ? { ...message.state, status: 'stale' as const } : message.state;
          const next = { ...current.current, state: { ...current.current.state, hypothesis_state: panel } };
          current.current = next; setStudy(next);
        }
        break;
      case 'study_state': applyStudy(message); break;
      case 'observer_status':
        revision.current = Math.max(revision.current, message.revision ?? 0);
        setObserver(message);
        break;
      case 'ack':
        revision.current = Math.max(revision.current, message.revision);
        responseRevision.current = Math.max(responseRevision.current, message.response_revision ?? 0);
        if (message.action === 'edit') {
          pendingEdits.current = Math.max(0, pendingEdits.current - 1);
          if (!pendingEdits.current && JSON.stringify(responseValue()) === sent.current) localStorage.removeItem(draftPrefix + sid);
        }
        if (message.event_type === 'student_message' && message.content) {
          appendHistory({ id: message.event_id, type: 'student_message', data: { content: message.content } });
        }
        break;
      case 'intervention': {
        if(message.plan) applyPlan({ type: 'plan', plan: message.plan, plan_version: message.plan_version ?? 0, plan_suggestion: message.plan_suggestion });
        const stale = JSON.stringify(responseValue()) !== sent.current || pendingEdits.current > 0 || message.revision < revision.current;
        if (message.proactive && stale) break;
        const { result } = message;
        if (result.type === 'highlight' && !stale) {
          const items = result.metadata.annotations ?? [];
          setAnnotations(items.every(item => annotationRange(workspace.current, item)) ? items : []);
          setDiagramAnnotations((result.metadata.diagram_annotations ?? []).filter(item =>
            diagram.current[item.target === 'node' ? 'nodes' : 'edges'].some(element => element.id === item.id)));
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
        if (current.current) {
          const next = { ...current.current, state: { ...current.current.state, assessment_stages: message.stages } };
          current.current = next; setStudy(next);
        }
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
      return ((!view.state.current_problem.id) || (!view.state.plan?.steps?.length)
        || (view.state.current_problem.solved && !view.state.study_complete)) && !view.state.ended
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
          const data = await api<{ history: HistoryItem[] }>(path);
          if (stopped || ws.current !== socket) return;
          const saved = data.history.filter(item => item.type === 'student_message' || item.type === 'intervention');
          setHistory(items => [...saved, ...items.filter(item => !saved.some(old => old.id === item.id))]
            .sort((a, b) => a.id - b.id));
          const view = await restore();
          if (stopped || ws.current !== socket || socket.readyState !== WebSocket.OPEN) return;
          applyStudy(view);
          if (opened) sent.current = JSON.stringify(view.state.response ?? { text: view.state.workspace, diagram: emptyDiagram() });
          opened = true;
          flushEdits();
          setConnected(true);
          setNotice('');
        } catch (error) {
          if (!stopped) setNotice((error as Error).message);
        }
      };
      socket.onclose = event => {
        if (stopped || ws.current !== socket) return;
        setConnected(false);
        setBusy(false);
        if (event.code === 1008) {
          window.dispatchEvent(new Event(authExpired));
          return;
        }
        reconnect = window.setTimeout(connect, 2000);
      };
    }
    connect();
    const idle = window.setInterval(() => {
      if (!document.hidden && current.current?.state.current_problem.id
          && !current.current.state.current_problem.solved && !current.current.state.study_complete && !current.current.state.ended
          && ws.current?.readyState === WebSocket.OPEN) send({ type: 'student_action', action: 'idle' });
    }, 10000);
    return () => {
      stopped = true;
      window.clearInterval(idle);
      window.clearTimeout(reconnect);
      window.clearTimeout(editTimer.current);
      ws.current?.close();
      ws.current = null;
    };
  }, [sid, uploading]);

  function upload(files: File[]) {
    const form = new FormData();
    for (const file of files) form.append('files', file);
    void begin('Building one learning graph from your materials…',
      () => api<{ id: string }>('/api/materials', { method: 'POST', body: form }));
  }

  function studyTopic(event: FormEvent) {
    event.preventDefault();
    const value = topic.trim();
    if (value.length < 2) return;
    void begin('Searching the web for “' + value + '” and building your learning graph…',
      () => post<{ id: string }>('/api/materials/web', { topic: value }));
  }

  async function begin(message: string, makeMaterial: () => Promise<{ id: string }>) {
    setUploading(true);
    setProgressMessage(message);
    onUploadChange(true);
    setConnected(false);
    setNotice('');
    try {
      const material = await makeMaterial();
      setTopic('');
      const session = await api<{ id: number }>('/api/sessions', { method: 'POST' });
      const next = await post<Study>('/api/sessions/' + session.id + '/study', { material_id: material.id });
      setHistory([]);
      setChat(''); setSummary(''); setAssessment(''); setMemoryActivity('');
      revision.current = 0;
      applyStudy(next, session.id);
      setSid(session.id);
      onCreated(session.id);
    } catch (error) {
      setNotice((error as Error).message);
    } finally {
      setUploading(false);
      onUploadChange(false);
      onSessionsChange();
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
    const response = responseValue();
    if ((!response.text.trim() && !response.diagram.nodes.length) || disabled || !flushEdits()) return;
    if (send({ type: 'problem_attempt', response, problem_id: problem?.id })) {
      setPending(true); markDraft('analyzing');
    }
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
      {uploading ? progressMessage : study?.state.study_complete ? 'Learning target reached'
        : study?.state.ended ? 'Session ended' : study && !connected ? 'Connecting…'
        : study?.material.name ?? 'Upload material or search a topic to begin'}
    </span><label className="upload" title="Select one or more PDF, TXT or Markdown files (up to 10)">Upload materials
      <input ref={uploadInput} id="upload" type="file" accept=".pdf,.txt,.md" multiple
      disabled={uploading} onChange={event => {
        const files = Array.from(event.target.files ?? []);
        event.target.value = '';
        if (files.length) upload(files);
      }} /></label>
      <form className="web-topic" role="search" onSubmit={studyTopic}>
        <input id="web-topic" type="search" placeholder="Or study a topic from the web…" aria-label="Topic to study from the web"
          value={topic} maxLength={200} disabled={uploading} onChange={event => setTopic(event.target.value)} />
        <button id="web-search" type="submit" disabled={uploading || topic.trim().length < 2}>Search web</button>
      </form>
      <div className="account-controls"><span>{user.email}</span><button type="button"
        disabled={uploading || pending || busy} onClick={async () => {
          try {
            flushEdits();
            await logout();
          } catch { setNotice('Could not log out. Please try again.'); }
        }}>Log out</button></div>
    </header>
    {notice && <p id="notice" role="alert">{notice}</p>}
    <main className={study ? 'study-layout' : 'new-chat'}>
      {!study ? <section className="new-chat-content">
        <p className="eyebrow">{sid ? 'STUDY CHAT' : 'NEW CHAT'}</p>
        <h1>{sid ? 'Opening your chat…' : 'What would you like to learn?'}</h1>
        <p>{sid ? 'Restoring your conversation and workspace.' : 'Upload your notes or search a topic to start a study chat. Earlier chats are in the sidebar.'}</p>
        <button type="button" disabled={uploading} onClick={() => uploadInput.current?.click()}>
          {uploading ? 'Preparing your study chat…' : 'Upload your notes'}
        </button>
        {uploading && <p role="status">{progressMessage}</p>}
        <p className="muted">PDF, Markdown, or text</p>
      </section> : <>
        <section className="panel chat chat-panel" aria-label="Tutor chat">
          <h2>Tutor chat</h2>
          <div ref={conversation} id="interventions" role="log" aria-live="polite">
            {!history.length && <p className="muted">Ask a question or use a teaching tool to start the conversation.</p>}
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
          <form onSubmit={event => { event.preventDefault(); ask(chat); }}>
            <label htmlFor="chat">Ask your tutor</label>
            <div className="answer-row"><input id="chat" maxLength={20000} value={chat} onChange={event => setChat(event.target.value)} disabled={disabled} />
              <button disabled={disabled || busy || !chat.trim()}>Send</button></div>
          </form>
        </section>
      <section className="panel question-panel">
        <p className="eyebrow">CURRENT TOPIC</p>
        <h1 id="concept">{study?.state.study_complete ? 'Material complete' : study?.state.current_concept ?? 'Your next learning step'}</h1>
        <div className="mastery"><span id="mastery">Estimated mastery: {mastery == null ? 'Not assessed yet' : Math.round(mastery * 100) + '%'}</span>
          {mastery != null && <progress max={1} value={mastery} aria-label="Estimated mastery" />}</div>
        <p className="muted">An estimate from your answers.</p>
        <p id="problem" className="problem">{study?.state.study_complete ? 'Every concept has reached the current progression target.'
          : problem?.prompt ?? 'Upload lecture notes or search the web for a topic to build a concept graph and get your first question.'}</p>
        <div id="observer-status" role="status">Observer: {observer.decision ?? 'Waiting'} · {observer.reason ?? 'Waiting for activity'}</div>
        <details><summary>Observer details</summary><pre>{JSON.stringify(observer, null, 2)}</pre></details>
        {sid && study && <LearningPlanPanel key={sid + ':' + study.material.id} sessionId={sid}
          value={{ type: 'plan', plan: study.state.plan ?? {}, plan_version: study.state.plan_version ?? 0, plan_suggestion: study.state.plan_suggestion }}
          disabled={!connected || uploading || study.state.ended} onChange={value => { if(current.current?.material.id === study.material.id) applyPlan(value); }} />}
        {problem?.format && <p className="eyebrow">{label(problem.format)}</p>}
        {problem?.diagram && <DiagramView value={problem.diagram} label="Question diagram" />}
        <h3>Your response</h3>
        <section className="response-workspace" aria-label="Your response">
        <Workspace key={problem?.id ?? 'empty'} initial={workspace.current} disabled={uploading || !problem?.id || inactive}
          annotations={annotations} canAsk={!disabled && !busy}
          onAsk={item => ask('Please explain your annotation on line '
            + item.line + ' about “' + item.quote + '”: ' + item.reason)}
          onDismiss={item => setAnnotations(items => items.filter(candidate => candidate !== item))}
          onEdit={text => {
            workspace.current = text;
            setAnnotations([]);
            saveDraft();
          }} />
        <details open={Boolean(diagramValue.nodes.length || problem?.format === 'diagram_construction')}>
          <summary>Optional node-and-arrow diagram</summary>
          <DiagramView value={diagramValue} disabled={uploading || !problem?.id || inactive} annotations={diagramAnnotations}
            onChange={value => {
              const previous = diagram.current;
              setDiagramAnnotations(items => items.filter(item => {
                const before = previous[item.target === 'node' ? 'nodes' : 'edges'].find(element => element.id === item.id);
                const after = value[item.target === 'node' ? 'nodes' : 'edges'].find(element => element.id === item.id);
                if (!before || !after || JSON.stringify(before) !== JSON.stringify(after)) return false;
                if (item.target === 'edge') {
                  const edge = previous.edges.find(element => element.id === item.id)!;
                  return [edge.source, edge.target].every(id => previous.nodes.find(node => node.id === id)?.label === value.nodes.find(node => node.id === id)?.label);
                }
                return true;
              }));
              diagram.current = value; setDiagramValue(value); saveDraft();
            }} />
        </details>
        <form onSubmit={submit} id="answer-form">
          <button id="submit" disabled={disabled || Boolean(problem?.solved)}>Submit response</button>
        </form>
        </section>
        <Hypotheses state={study?.state.hypothesis_state} />
        {study?.state.assessment_stages?.reflection === 'unavailable' && <p role="status">Reflection unavailable. Your assessment is saved.</p>}
        {study?.state.assessment_stages?.hypotheses === 'unavailable' && <p role="status">Submission hypothesis analysis unavailable. Using previous hypotheses.</p>}
        {study?.state.question_generation_error && <div role="status"><p>{study.state.question_generation_error}</p>
          <button type="button" disabled={busy || pending || !connected} onClick={async () => {
            setPending(true);
            try { applyStudy(await post<Study>('/api/sessions/' + sid + '/next', { problem_id: problem?.id })); }
            catch (error) { setNotice((error as Error).message); } finally { setPending(false); }
          }}>Retry next question</button></div>}
        <div id="manual-tools">{tools.map(tool => <button type="button" key={tool} data-tool={tool} disabled={disabled || busy}
          onClick={() => {
            if (flushEdits() && send({ type: 'student_action', action: 'hint_request', tool })) setBusy(true);
          }}>{label(tool)}</button>)}
          <button id="skip" type="button" disabled={disabled || busy} onClick={skip}>Skip question</button></div>
        <p id="assessment" aria-live="polite">{assessment}</p>
        {memoryActivity && <details className="memory-notice"><summary>Learning update</summary><p>{memoryActivity}</p></details>}
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
          {study.material.web_sources?.length ? <ul id="material-sources" className="muted">
            {study.material.web_sources.map(link => <li key={link.url}><a href={link.url} target="_blank"
              rel="noopener noreferrer">{link.title}</a>{link.concept ? ' (looked up for ' + link.concept + ')' : ''}</li>)}</ul>
            : (study.material.sources?.length ?? 0) > 1 && <ul id="material-sources" className="muted">
            {study.material.sources!.map(source => <li key={source}>{source}</li>)}</ul>}
          <pre>{study.material.chunks[0]?.text}</pre></details>}
      </aside>
      </>}
    </main>
  </MotionConfig>;
}

type SavedSession = { id: number; material_id?: string; title: string; created_at: string; ended: boolean; complete: boolean };

function ChatSessions({ user, logout }: { user: User; logout: () => Promise<void> }) {
  const [sessions, setSessions] = useState<SavedSession[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [version, setVersion] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    void api<{ sessions: SavedSession[] }>('/api/sessions').then(data => {
      if (active) { setSessions(data.sessions.filter(session => session.material_id)); setError(''); }
    }).catch(failure => { if (active) setError((failure as Error).message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);

  return <div className="chat-app">
    <aside className="session-sidebar" aria-label="Chat sessions">
      <strong className="sidebar-brand">Studyroom</strong>
      <button className="new-chat-button" type="button" disabled={uploading} onClick={() => {
        setSelected(null); setVersion(value => value + 1); setRefresh(value => value + 1);
      }}>＋ New chat</button>
      <p className="eyebrow">YOUR CHATS</p>
      {loading && <p role="status" className="muted">Loading chats…</p>}
      {error && <div role="alert"><p>{error}</p><button type="button"
        onClick={() => setRefresh(value => value + 1)}>Retry</button></div>}
      {!loading && !error && !sessions.length && <p className="muted">Your study chats will appear here.</p>}
      <nav aria-label="Previous chats">
        {sessions.map(session => <button key={session.id} type="button" className="session-item"
          aria-current={selected === session.id ? 'page' : undefined} disabled={uploading}
          onClick={() => setSelected(session.id)}>
          <span className="session-title">{session.title}</span>
          <span className="session-date">{new Date(session.created_at).toLocaleDateString(undefined, {
            month: 'short', day: 'numeric', year: 'numeric',
          })}{session.ended ? ' · Ended' : session.complete ? ' · Complete' : ''}</span>
        </button>)}
      </nav>
    </aside>
    <div className="chat-main">
      <App key={(selected ?? 'new') + ':' + version} user={user} logout={logout}
        initialSession={selected} onCreated={setSelected} onUploadChange={setUploading}
        onSessionsChange={() => setRefresh(value => value + 1)} />
    </div>
  </div>;
}

createRoot(document.getElementById('root')!).render(
  <Auth>{(user, logout) => <ChatSessions key={user.id} user={user} logout={logout} />}</Auth>,
);

