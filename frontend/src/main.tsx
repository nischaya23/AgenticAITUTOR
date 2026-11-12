import { useEffect, useRef, useState, type FormEvent } from 'react';
import { createRoot } from 'react-dom/client';
import { createPortal } from 'react-dom';
import { MotionConfig } from 'motion/react';
import Auth, { authExpired, type User } from './Auth';
import Icon from './Icon';
import { ThemeProvider, ThemeToggle } from './Theme';
import LearningPlanPanel from './LearningPlan';
import Hypotheses from './Hypotheses';
import Workspace, { annotationRange } from './Workspace';
import { FlowGraph, VisualPlayer } from './Visualization';
import MemoryPanel from './Memory';
import { PreparationPanel } from './Progress';
import ActivityFlow, { updateTutorFlow, type TutorStep, type WorkflowEvent } from './ActivityFlow';
import type { Annotation, Assessment, HypothesisState, Response as StudentResponse, HistoryItem, Observer, PlanState, Result, Study, Tool, ProgressEvent } from './types';
import '../styles.css';

const tools: Tool[] = ['hint', 'explanation', 'highlight', 'visualization', 'guiding_question', 'web_search'];
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
  workflow_run_id?: string; type: 'intervention'; event_id: number; proactive: boolean; result: Result; revision: number;
} | { type: 'assessment'; result: Assessment; stages?: { reflection: string; hypotheses: string } }
  | { type: 'hypotheses'; state: HypothesisState }
  | { type: 'memory_activity'; items: { message: string }[] }
  | { type: 'ack'; workflow_run_id?: string; event_id: number; event_type: string; action: string; revision: number; response_revision?: number; content?: string }
  | ({ type: 'observer_status' } & Observer)
  | { type: 'busy'; active: boolean }
  | { type: 'workflow_events'; events: WorkflowEvent[]; watermark: number }
  | { type: 'session_summary'; result: { summary: string; next_goal: string } }
  | { type: 'memory_changed' }
  | ({ type: 'research_progress' } & ProgressEvent)
  | { type: 'error'; message: string };

function Intervention({ result }: { result: Result }) {
  const { metadata } = result;
  const visual = metadata.visualization;
  return <>
    <strong>{label(result.type)}</strong><p>{result.content}</p>
    {visual && ['slideshow', 'graph_traversal', 'tree_traversal', 'concept_graph'].includes(visual.kind)
      && <VisualPlayer data={visual} />}
    {!visual && metadata.nodes && metadata.nodes.length > 0 && metadata.edges && <VisualPlayer data={{
      kind: 'slideshow', title: 'Visualization', nodes: metadata.nodes, edges: metadata.edges,
      steps: (metadata.steps ?? []).map(explanation => ({ explanation, active_nodes: [] })),
    }} />}
    {metadata.retrieved_sources?.length ? <details><summary>Retrieved sources</summary><ul>{metadata.retrieved_sources.map(source =>
      <li key={source.id}>{source.url ? <a href={source.url} target="_blank" rel="noopener noreferrer">{source.source}</a> : source.source}</li>)}</ul></details> : null}
  </>;
}

function App({ user, logout, initialSession, onCreated, onUploadChange, onSessionsChange, onMenu, menuOpen, logoutTarget }: {
  user: User; logout: () => Promise<void>; initialSession: number | null;
  onCreated: (id: number) => void; onUploadChange: (uploading: boolean) => void; onSessionsChange: () => void;
  onMenu: () => void; menuOpen: boolean;
  logoutTarget: HTMLElement | null;
}) {
  const draftPrefix = 'studyroom-draft-' + user.id + '-';
  const [sid, setSid] = useState<number | null>(initialSession);
  const [study, setStudy] = useState<Study | null>(null);
  const [observer, setObserver] = useState<Observer>({});
  const [notice, setNotice] = useState('');
  const [uploading, setUploading] = useState(false);
  const [connected, setConnected] = useState(false);
  const [pending, setPending] = useState(false);
  const [busy, setBusy] = useState(false);
  const [chat, setChat] = useState('');
  const [assessment, setAssessment] = useState('');
  const [tutorSteps, setTutorSteps] = useState<TutorStep[]>([]);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [annotations, setAnnotations] = useState<Annotation[]>([]);
  const [summary, setSummary] = useState('');
  const [studyTab, setStudyTab] = useState<'workspace' | 'map' | 'memory'>('workspace');
  const ws = useRef<WebSocket | null>(null);
  const current = useRef<Study | null>(null);
  const workspace = useRef('');
  const sent = useRef('');
  const revision = useRef(0);
  const responseRevision = useRef(0);
  const pendingEdits = useRef(0);
  const editTimer = useRef<number | undefined>(undefined);
  const conversation = useRef<HTMLDivElement>(null);
  const conversationAtBottom = useRef(true);

  useEffect(() => {
    const element = conversation.current;
    if (element && conversationAtBottom.current) element.scrollTop = element.scrollHeight;
  }, [history.length, study?.material.id]);

  function send(event: object) {
    if (ws.current?.readyState !== WebSocket.OPEN) {
      setNotice('Connection interrupted. Reconnecting; your work remains here.');
      return false;
    }
    ws.current.send(JSON.stringify(event));
    return true;
  }

  function responseValue(): StudentResponse {
    return { text: workspace.current };
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
    const serverResponse = { text: view.state.response?.text ?? view.state.workspace };
    if(previous?.material.id === view.material.id && (previous.state.plan_version ?? 0) > (view.state.plan_version ?? 0)){
      view = { ...view, state: { ...view.state, plan: previous.state.plan, plan_version: previous.state.plan_version, plan_suggestion: previous.state.plan_suggestion } };
    }
    const changed = current.current?.state.current_problem.id !== view.state.current_problem.id
      || current.current?.material.id !== view.material.id;
    current.current = view;
    if (changed) {
      setStudyTab('workspace');
      window.clearTimeout(editTimer.current);
      const saved = serverResponse;
      sent.current = JSON.stringify(saved);
      let restored = saved;
      try {
        const local = JSON.parse(localStorage.getItem(draftPrefix + sessionId) ?? 'null');
        if (local?.questionId === view.state.current_problem.id && typeof local.response?.text === 'string') restored = {text: local.response.text};
      } catch { /* A malformed local draft cannot replace the server copy. */ }
      workspace.current = restored.text.replace(/\r\n?/g, '\n');
      view = { ...view, state: { ...view.state, response: restored, workspace: restored.text } };
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
    setMemoryVersion(version => version + 1);
    const active = current.current;
    if (!active || value.plan_version < (active.state.plan_version ?? 0)) return;
    const next = { ...active, state: { ...active.state, plan: value.plan, plan_version: value.plan_version, plan_suggestion: value.plan_suggestion,
      hypothesis_state: active.state.hypothesis_state && value.plan_version !== active.state.plan_version
        ? { ...active.state.hypothesis_state, status: 'stale' as const } : active.state.hypothesis_state } };
    current.current = next; setStudy(next);
  }

  function onMessage(message: Message) {
    switch (message.type) {
      case 'memory_changed': setMemoryVersion(value => value + 1); break;
      case 'research_progress': break;
      case 'workflow_events':
        setTutorSteps(steps => updateTutorFlow(steps, message.events));
        break;
      case 'memory_activity': break;
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
        setMemoryVersion(value => value + 1);
        revision.current = Math.max(revision.current, message.revision);
        responseRevision.current = Math.max(responseRevision.current, message.response_revision ?? 0);
        if (message.action === 'edit') {
          pendingEdits.current = Math.max(0, pendingEdits.current - 1);
          if (!pendingEdits.current && JSON.stringify(responseValue()) === sent.current) localStorage.removeItem(draftPrefix + sid);
        }
        if (message.event_type === 'student_message' && message.content) {
          appendHistory({ id: message.event_id, workflow_run_id: message.workflow_run_id, type: 'student_message', data: { content: message.content } });
        }
        break;
      case 'intervention': {
        if(message.plan) applyPlan({ type: 'plan', plan: message.plan, plan_version: message.plan_version ?? 0, plan_suggestion: message.plan_suggestion });
        const stale = JSON.stringify(responseValue()) !== sent.current || pendingEdits.current > 0 || message.revision < revision.current;
        if (message.proactive && stale) break;
        const { result } = message;
        if (result.type === 'highlight' && !stale) {
          setStudyTab('workspace');
          const items = result.metadata.annotations ?? [];
          setAnnotations(items.every(item => annotationRange(workspace.current, item)) ? items : []);
        }
        appendHistory({ id: message.event_id, workflow_run_id: message.workflow_run_id, type: 'intervention',
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
      const socket = new WebSocket(protocol + '//' + location.host + '/ws/' + sid + '?workflow=1');
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
          if (opened) sent.current = JSON.stringify(view.state.response ?? { text: view.state.workspace });
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
        setTutorSteps([]);
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

  const [topic, setTopic] = useState('');
  const [preparation, setPreparation] = useState<{id: string | null; upload: boolean} | null>(null);
  const startingPreparation = useRef(false);
  const [memoryVersion, setMemoryVersion] = useState(0);

  async function startPreparation(files?: File[]) {
    if (uploading || startingPreparation.current) return;
    startingPreparation.current = true;
    setUploading(true); onUploadChange(true); setNotice('');
    const upload = Boolean(files);
    setPreparation({id: null, upload});
    try {
      let job: {id: string};
      if (files) {
        const form = new FormData(); files.forEach(file => form.append('files', file));
        job = await api('/api/material-jobs', {method: 'POST', body: form});
      } else job = await post('/api/material-jobs/topic', {description: topic.trim()});
      setPreparation({id: job.id, upload});
    } catch (error) {
      setPreparation(preparation); setNotice((error as Error).message); setUploading(false); onUploadChange(false);
    } finally { startingPreparation.current = false; }
  }

  async function openPrepared(materialId: string, preparationId: string) {
    setUploading(true); onUploadChange(true);
    setNotice('');
    try {
      const session = await post<{ id: number }>('/api/sessions', { preparation_id: preparationId });
      const next = await post<Study>('/api/sessions/' + session.id + '/study', { material_id: materialId });
      setHistory([]); setChat(''); setSummary(''); setAssessment(''); setTutorSteps([]);
      revision.current = 0; applyStudy(next, session.id); setSid(session.id); onCreated(session.id);
      setPreparation(null);
    } catch (error) { setNotice((error as Error).message); }
    finally { setUploading(false); onUploadChange(false); onSessionsChange(); }
  }

  function ask(content: string) {
    if (disabled || busy || !content.trim() || !flushEdits()) return;
    if (send({ type: 'student_message', content: content.trim() })) {
      setTutorSteps([]);
      setBusy(true);
      setChat('');
    }
  }

  function requestTool(tool: Tool) {
    if (!disabled && !busy && flushEdits() && send({ type: 'student_action', action: 'hint_request', tool })) {
      setTutorSteps([]);
      setBusy(true);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const response = responseValue();
    if (!response.text.trim() || disabled || !flushEdits()) return;
    if (send({ type: 'problem_attempt', response, problem_id: problem?.id })) {
      setTutorSteps([]);
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

  async function chooseConcept(concept: string) {
    if (!sid || !flushEdits()) return;
    setPending(true);
    try {
      applyStudy(await post<Study>('/api/sessions/' + sid + '/concept', { concept }));
      setAssessment('Switched to ' + concept + '. The tutor stays on it until you master it.');
    } catch (error) {
      setNotice((error as Error).message);
    } finally { setPending(false); }
  }

  async function setWebEnhance(enabled: boolean) {
    if (!study) return;
    try {
      const result = await post<{ web_enhance: boolean }>('/api/materials/' + study.material.id + '/web-enhance', { enabled });
      if (current.current?.material.id === study.material.id)
        applyStudy({ ...current.current, material: { ...current.current.material, web_enhance: result.web_enhance } });
    } catch (error) {
      setNotice((error as Error).message);
    }
  }

  const problem = study?.state.current_problem;
  const inactive = Boolean(study?.state.ended || study?.state.study_complete);
  const disabled = uploading || !connected || !problem?.id || inactive || pending;
  const choosing = uploading || !connected || pending || busy || Boolean(study?.state.ended || study?.state.assessment_pending);
  const activeNode = study?.graph.nodes.find(node => node.active);
  const mastery = activeNode?.mastery;
  const tutorStatus = study?.state.ended ? 'Session ended' : study?.state.study_complete ? 'Learning target reached'
    : !connected ? 'Connecting…' : 'Ready when you are';
  const showTutorFlow = connected && !inactive && (tutorSteps.length > 0 || busy || pending);

  function renderMessage(item: HistoryItem) {
    if (item.type === 'student_message') {
      return <article className="student-message" key={item.id}><strong>You</strong><p>{item.data.content}</p></article>;
    }
    return <article className="intervention" key={item.id}>
      <Intervention result={item.data} />
      {observer.feedback_intervention_id === item.id && !inactive &&
        <div className="player-controls">{([1, -1] as const).map(rating =>
          <button type="button" key={rating} disabled={!connected || busy} onClick={() => {
            if (send({ type: 'feedback', intervention_id: item.id, rating })) setObserver(status =>
              ({ ...status, feedback_intervention_id: null }));
          }}>{rating === 1 ? 'Helpful' : 'Not helpful'}</button>)}</div>}
    </article>;
  }

  return <MotionConfig reducedMotion="user">
    <div className="mobile-toolbar">
      <button className="icon-button mobile-menu" type="button" aria-label="Open chat sessions"
        aria-expanded={menuOpen} onClick={onMenu}><Icon name="menu" /></button>
    </div>
    {logoutTarget && createPortal(<button className="logout-button" type="button"
        disabled={uploading || pending || busy} onClick={async () => {
          try {
            flushEdits();
            await logout();
          } catch { setNotice('Could not log out. Please try again.'); }
        }}>Log out</button>, logoutTarget)}
    {notice && <p id="notice" role="alert">{notice}</p>}
    <main className={study ? 'study-layout' : 'new-chat'}>
      {!study ? <section className="new-chat-content">
        <span className="hero-symbol"><Icon name="book" /></span>
        <h1>{sid ? 'Opening your chat…' : 'What would you like to learn?'}</h1>
        {!sid && <>
          <form className="topic-form" onSubmit={event => {event.preventDefault(); void startPreparation();}}>
            <label htmlFor="topic-description">Describe your topic and learning goals</label>
            <textarea id="topic-description" rows={4} maxLength={10000} value={topic} onChange={event => setTopic(event.target.value)}
              disabled={uploading} placeholder="For example: Teach me binary search from the beginning, especially boundary conditions." />
            <button disabled={uploading || topic.trim().length < 2}>Build my learning map</button>
          </form>
          <p className="muted">Or start from your own notes</p>
          <label className="upload upload-card">
            <Icon name="upload" /><span>Choose files</span>
            <input id="upload" type="file" accept=".pdf,.txt,.md" multiple disabled={uploading}
              onChange={event => {
                const files = Array.from(event.target.files ?? []);
                event.target.value = '';
                if (files.length) void startPreparation(files);
              }} />
          </label>
          <p className="muted">PDF, Markdown, or text</p>
        </>}
        {preparation && <PreparationPanel key={preparation.id ?? 'starting'} id={preparation.id} upload={preparation.upload}
          onReady={openPrepared} onWaiting={()=>{setUploading(false);onUploadChange(false);}} />}
      </section> : <>
      <div className="study-dock">
        <div className="dock-tabs" role="tablist" aria-label="Study tools" onKeyDown={event => {
          if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
          event.preventDefault();
          const tabs = ['workspace','map','memory'] as const;
          const index = tabs.indexOf(studyTab);
          const next = tabs[event.key === 'Home' ? 0 : event.key === 'End' ? 2 : (index + (event.key === 'ArrowRight' ? 1 : 2)) % 3];
          setStudyTab(next); document.getElementById(next + '-tab')?.focus();
        }}>
          <button id="workspace-tab" role="tab" aria-selected={studyTab === 'workspace'} aria-controls="workspace-panel"
            tabIndex={studyTab === 'workspace' ? 0 : -1}
            onClick={() => setStudyTab('workspace')}>Workspace</button>
          <button id="map-tab" role="tab" aria-selected={studyTab === 'map'} aria-controls="map-panel"
            tabIndex={studyTab === 'map' ? 0 : -1}
            onClick={() => setStudyTab('map')}>Learning map</button>
          <button id="memory-tab" role="tab" aria-selected={studyTab === 'memory'} aria-controls="memory-panel"
            tabIndex={studyTab === 'memory' ? 0 : -1} onClick={() => setStudyTab('memory')}>Memory</button>
        </div>
      <section id="workspace-panel" role="tabpanel" aria-labelledby="workspace-tab" hidden={studyTab !== 'workspace'} className="panel question-panel">
        <p className="eyebrow">CURRENT TOPIC</p>
        <h1 id="concept">{study?.state.study_complete ? 'Material complete' : study?.state.current_concept ?? 'Your next learning step'}</h1>
        <div className="mastery"><span id="mastery">Estimated mastery: {mastery == null ? 'Not assessed yet' : Math.round(mastery * 100) + '%'}</span>
          {mastery != null && <progress max={1} value={mastery} aria-label="Estimated mastery" />}</div>
        <p className="muted">An estimate from your answers.</p>
        <p id="problem" className="problem">{study?.state.study_complete ? 'Every concept has reached the current progression target.'
          : problem?.prompt ?? 'Your first question will appear here.'}</p>
        <label className="web-enhance-option"><input type="checkbox" checked={study.state.proactive_enabled !== false}
          disabled={!connected || inactive} onChange={event=>{
            const enabled=event.target.checked;
            if(send({type:'preferences',proactive_enabled:enabled})) applyStudy({...study,state:{...study.state,proactive_enabled:enabled}});
          }} />Offer automatic help</label>
        {observer.reason && <p className="muted" id="observer-status">{observer.reason}</p>}
        {sid && study && <details className="plan-details"><summary>Your learning plan</summary><LearningPlanPanel key={sid + ':' + study.material.id} sessionId={sid}
          value={{ type: 'plan', plan: study.state.plan ?? {}, plan_version: study.state.plan_version ?? 0, plan_suggestion: study.state.plan_suggestion }}
          disabled={!connected || uploading || study.state.ended} onChange={value => { if(current.current?.material.id === study.material.id) applyPlan(value); }} /></details>}
        {problem?.format && <p className="eyebrow">{label(problem.format)}</p>}
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
        <button id="skip" className="text-button" type="button" disabled={disabled || busy} onClick={skip}>Skip question</button>
        <p id="assessment" aria-live="polite">{assessment}</p>
        {study && !study.state.ended && <button type="button" disabled={!connected || pending || busy || uploading}
          onClick={() => { if (flushEdits() && send({ type: 'session_end' })) setPending(true); }}>End session</button>}
        {summary && <p role="status">{summary}</p>}
      </section>
      <section id="map-panel" role="tabpanel" aria-labelledby="map-tab" hidden={studyTab !== 'map'} className="panel graph-panel">
        <p className="eyebrow">YOUR LEARNING MAP</p><h2>Concept graph</h2>
        <p className="muted">Arrows point from a prerequisite to the next concept. Click any unlocked concept to study it next.</p>
        <div id="graph">{study ? <FlowGraph key={study.material.id} graph={study.graph}
          masteryTarget={study.mastery_target} label="Concept graph and estimated mastery"
          onSelect={choosing ? undefined : concept => void chooseConcept(concept)} />
          : <p className="muted">Your map will appear after processing.</p>}</div>
        {study && <div className="concept-controls">
          <label htmlFor="concept-picker">Study a concept</label>
          <select id="concept-picker" value="" disabled={choosing} onChange={event => {
            if (event.target.value) void chooseConcept(event.target.value);
          }}>
            <option value="">Choose an unlocked concept…</option>
            {study.graph.nodes.filter(node => node.available && !node.active).map(node =>
              <option key={node.id} value={node.id}>{node.label}</option>)}
          </select>
          <label className="web-enhance-option"><input id="web-enhance" type="checkbox"
            checked={Boolean(study.material.web_enhance)} disabled={study.state.ended}
            onChange={event => void setWebEnhance(event.target.checked)} />
            Enhance with web search for every concept</label>
        </div>}
        {study && <details><summary id="material-name">{study.material.name}</summary>
          {study.material.web_sources?.length ? <ul id="material-sources" className="muted">
            {study.material.web_sources.map(link => <li key={link.url}><a href={link.url} target="_blank"
              rel="noopener noreferrer">{link.title}</a>{link.concept ? ' (looked up for ' + link.concept + ')' : ''}</li>)}</ul>
            : (study.material.sources?.length ?? 0) > 1 && <ul id="material-sources" className="muted">
            {study.material.sources!.map(source => <li key={source}>{source}</li>)}</ul>}
          <details><summary>Graph evidence</summary><ul>{study.material.graph?.evidence?.map((e,index) => {
            const source=study.material.graph_sources?.find(item=>item.id===e.source_id);
            return <li key={index}><strong>{e.prerequisite ? e.prerequisite + ' → ' : ''}{e.concept}</strong>
              <blockquote>{e.quote}</blockquote>{source?.url ? <a href={source.url} target="_blank" rel="noopener noreferrer">{source.title}</a> : source?.title}</li>;
          })}</ul></details><pre>{study.material.chunks[0]?.text}</pre></details>}
      </section>
      <section id="memory-panel" role="tabpanel" aria-labelledby="memory-tab" hidden={studyTab !== 'memory'} className="panel memory-panel">
        {sid && <MemoryPanel sessionId={sid} active={studyTab === 'memory'} version={memoryVersion} />}
      </section>
      </div>
        <section className="panel chat chat-panel" aria-label="Tutor chat">
          <div className="conversation-heading"><span className="tutor-avatar"><Icon name="spark" /></span>
            <div><h2>Tutor chat</h2>{!showTutorFlow && <p role="status"><span className={'status-dot' + (connected && !inactive ? ' online' : '')} />
              {tutorStatus}</p>}</div>
          </div>
          {showTutorFlow && <ActivityFlow label="Tutor progress"
            steps={tutorSteps.length ? tutorSteps : [{ id: 'working', label: 'Working on your request', state: 'working' }]} />}
          <div id="interventions" ref={conversation} role="log" aria-live="polite"
            onScroll={event => {
              const element = event.currentTarget;
              conversationAtBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 48;
            }}>
            {!history.length && <div className="conversation-welcome"><Icon name="chat" />
              <h3>Let’s work through it.</h3><p>Ask about {study.state.current_concept}, request a hint, or try the question in your workspace.</p>
              <button type="button" disabled={disabled || busy} onClick={() => requestTool('guiding_question')}>Help me get started<Icon name="arrow" /></button>
            </div>}
            {history.map(renderMessage)}
          </div>
          <form className="chat-composer" onSubmit={event => { event.preventDefault(); ask(chat); }}>
            <label className="sr-only" htmlFor="chat">Ask your tutor</label>
            <textarea id="chat" rows={2} placeholder="Ask a question, or tell me where you’re stuck…"
              maxLength={20000} value={chat} onChange={event => setChat(event.target.value)} disabled={disabled}
              onKeyDown={event => {
                if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
                  event.preventDefault(); event.currentTarget.form?.requestSubmit();
                }
              }} />
            <div className="composer-footer"><span>Enter to send · Shift + Enter for a new line</span>
              <button aria-label="Send" disabled={disabled || busy || !chat.trim()}><Icon name="arrow" /></button></div>
          </form>
          <div id="manual-tools">{tools.map(tool => <button type="button" key={tool} data-tool={tool} disabled={disabled || busy}
            onClick={() => requestTool(tool)}>{label(tool)}</button>)}</div>
        </section>
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
  const [menuOpen, setMenuOpen] = useState(false);
  const [logoutTarget, setLogoutTarget] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setMenuOpen(false); };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [menuOpen]);

  useEffect(() => {
    let active = true;
    void api<{ sessions: SavedSession[] }>('/api/sessions').then(data => {
      if (active) { setSessions(data.sessions.filter(session => session.material_id)); setError(''); }
    }).catch(failure => { if (active) setError((failure as Error).message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);

  return <div className={'chat-app' + (menuOpen ? ' menu-open' : '')}>
    {menuOpen && <button className="sidebar-backdrop" aria-label="Close chat sessions" onClick={() => setMenuOpen(false)} />}
    <aside className="session-sidebar" aria-label="Chat sessions">
      <div className="sidebar-heading"><strong className="sidebar-brand"><span className="brand-mark"><Icon name="book" /></span>Studyroom<span className="brand-dot">.</span></strong>
        <button className="icon-button mobile-menu" aria-label="Close chat sessions" onClick={() => setMenuOpen(false)}><Icon name="close" /></button></div>
      <button className="new-chat-button" type="button" disabled={uploading} onClick={() => {
        setSelected(null); setVersion(value => value + 1); setRefresh(value => value + 1);
        setMenuOpen(false);
      }}><Icon name="plus" />New chat<span className="new-chat-shortcut">↗</span></button>
      <div className="sidebar-section-title"><p className="eyebrow">RECENT CHATS</p><span>{sessions.length}</span></div>
      {loading && <p role="status" className="muted">Loading chats…</p>}
      {error && <div role="alert"><p>{error}</p><button type="button"
        onClick={() => setRefresh(value => value + 1)}>Retry</button></div>}
      {!loading && !error && !sessions.length && <p className="muted">Your study chats will appear here.</p>}
      <nav aria-label="Previous chats">
        {sessions.map(session => <button key={session.id} type="button" className="session-item"
          aria-current={selected === session.id ? 'page' : undefined} disabled={uploading}
          onClick={() => { setSelected(session.id); setMenuOpen(false); }}>
          <Icon name="chat" /><span className="session-copy"><span className="session-title">{session.title}</span>
          <span className="session-date">{new Date(session.created_at).toLocaleDateString(undefined, {
            month: 'short', day: 'numeric', year: 'numeric',
          })}{session.ended ? ' · Ended' : session.complete ? ' · Complete' : ''}</span></span>
        </button>)}
      </nav>
      <div className="sidebar-footer"><ThemeToggle />
        <div className="user-profile"><span className="user-avatar">{user.email.charAt(0).toUpperCase()}</span>
          <span><strong>Your workspace</strong><small title={user.email}>{user.email}</small></span></div>
        <div className="profile-actions" ref={setLogoutTarget} />
      </div>
    </aside>
    <div className="chat-main">
      <App key={(selected ?? 'new') + ':' + version} user={user} logout={logout}
        initialSession={selected} onCreated={setSelected} onUploadChange={setUploading}
        onSessionsChange={() => setRefresh(value => value + 1)} onMenu={() => setMenuOpen(true)} menuOpen={menuOpen}
        logoutTarget={logoutTarget} />
    </div>
  </div>;
}

createRoot(document.getElementById('root')!).render(
  <ThemeProvider><Auth>{(user, logout) => <ChatSessions key={user.id} user={user} logout={logout} />}</Auth></ThemeProvider>,
);

