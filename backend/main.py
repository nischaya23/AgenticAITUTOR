"""Local single-user FastAPI application; background agent jobs keep WS input live."""
import asyncio
import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from fastapi import FastAPI, HTTPException, UploadFile, File, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import Field, ValidationError
from sqlalchemy import select
from . import activity, config, knowledge, memory, observer, orchestrator, tools, utils
from .db import Session, Event, Concept, ConceptEdge, SessionLocal, init_db, local_user, append_event

SOCKETS: dict[int, set] = defaultdict(set)
TASKS: dict[int, set] = defaultdict(set)
LOCKS: dict[int, asyncio.Lock] = {}
LOG = logging.getLogger('tutor')


@asynccontextmanager
async def lifespan(app):
    init_db()
    with SessionLocal.begin() as db:
        knowledge.seed_knowledge(db)
        for row in db.scalars(select(Session)):
            if row.state.get('observer', {}).get('pending_wake'):
                orchestrator.clear_pending_wake(db, row.id)
    yield
    pending = [t for group in TASKS.values() for t in group]
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    SOCKETS.clear()
    TASKS.clear()
    LOCKS.clear()


app = FastAPI(title='Agentic AI Tutor', lifespan=lifespan)
FRONTEND_BUILD = config.ROOT / 'frontend' / 'dist'
app.mount('/assets', StaticFiles(directory=FRONTEND_BUILD / 'assets', check_dir=False), name='assets')


def observer_status(state):
    observation = state.get('observer', {})
    status = dict(observation.get('status') or {})
    status['revision'] = state.get('revision', 0)
    pending = observation.get('pending_intervention')
    status['feedback_intervention_id'] = (pending['event_id'] if pending
        and not pending.get('feedback_received') and utils.now() - pending['at'] <= 600 else None)
    if state.get('ended') or state.get('study_complete'):
        status.update(decision='INACTIVE', reason='Session ended or learning target reached')
    return status


def public_state(state: dict) -> dict:
    return {'observer_status': observer_status(state), **{k: v for k, v in state.items() if k != 'observer'},
        'current_problem': {k: v for k, v in state.get('current_problem', {}).items()
                            if k not in ('expected_answer', 'rubric')}}


def require_session(db, session_id: int):
    row = db.get(Session, session_id)
    if row is None:
        raise HTTPException(404, 'Session not found.')
    return row


@app.get('/')
def frontend():
    index = FRONTEND_BUILD / 'index.html'
    if not index.is_file():
        raise HTTPException(503, 'Build the frontend with npm ci and npm run build, or use the Vite dev server.')
    return FileResponse(index)


@app.get('/api/health')
def health():
    key = config.GEMINI_API_KEY if config.LLM_PROVIDER == 'gemini' else config.OPENAI_API_KEY
    return {'status': 'ok', 'demo_mode': config.DEMO_MODE, 'configured': bool(key)}


@app.post('/api/sessions')
def create_session():
    with SessionLocal.begin() as db:
        user = local_user(db)
        state = utils.SessionData(current_problem={
            'id': 'binary-search-first', 'concept': 'Binary Search',
            'prompt': 'For [1, 2, 2, 2, 5], give the zero-based index of the first occurrence of 2. Enter only the index.',
            'expected_answer': '1', 'rubric': 'First matching value is at zero-based index 1.'},
            observer={'problem_started_at': utils.now(), 'last_progress_at': utils.now(),
                      'last_action_at': utils.now()}).model_dump(mode='json')
        row = Session(user_id=user.id, state=state)
        db.add(row)
        db.flush()
        memory.update_long_term_memory(db, user.id, {})
        append_event(db, row.id, 'session_started', {'concept': 'Binary Search'})
        return {'id': row.id, 'state': public_state(state)}


@app.get('/api/sessions/{session_id}')
def read_session(session_id: int):
    with SessionLocal() as db:
        row = require_session(db, session_id)
        history = db.scalars(select(Event).where(Event.session_id == session_id,
            Event.type.in_(['student_message', 'intervention', 'assessment', 'reflection']))
            .order_by(Event.id.desc()).limit(60)).all()
        return {'id': row.id, 'state': public_state(row.state),
                'history': [{'id': e.id, 'type': e.type, 'data': e.data} for e in reversed(history)]}


@app.get('/api/student')
def read_student():
    with SessionLocal.begin() as db:
        data = memory.get_long_term_memory(db, local_user(db).id)
        return {k: v for k, v in data.items() if k != 'policy'}


class ProblemRequest(utils.Schema):
    concept: str = Field(min_length=1, max_length=200)


@app.post('/api/sessions/{session_id}/problem')
async def new_problem(session_id: int, request: ProblemRequest):
    with SessionLocal() as db:
        initial = require_session(db, session_id).state
        if initial['ended']:
            raise HTTPException(409, 'Start a new session first.')
        revision = initial['revision']
    try:
        problem = await tools.generate_problem(request.concept)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    with SessionLocal.begin() as db:
        row = require_session(db, session_id)
        state = dict(row.state)
        if state['ended'] or state['revision'] != revision:
            raise HTTPException(409, 'Work changed while generating a problem; try again.')
        state.update(current_problem=problem, current_concept=request.concept, workspace='', plan={},
                     revision=state['revision']+1, observer={})
        orchestrator.persist_observation(db, row, state, 'student_action', {'action': 'problem_opened'})
        append_event(db, session_id, 'student_action', {'action': 'problem_opened', 'problem_id': problem['id']})
        result = {'type': 'state', 'state': public_state(state)}
    await broadcast(session_id, result)
    return result


@app.get('/api/materials')
async def materials():
    records = await asyncio.to_thread(knowledge.list_materials)
    return [{'id': m['id'], 'name': m['name'], 'chunks': m['chunks']} for m in records]


@app.post('/api/materials')
async def upload_material(file: UploadFile = File(...)):
    content = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(content) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, 'Upload limit is 8 MiB.')
    name = Path(file.filename or 'material.txt').name
    try:
        text = await asyncio.to_thread(knowledge.extract_text, name, content)
        graph = await knowledge.extract_concepts(text)
        with SessionLocal.begin() as db:
            graph = knowledge.store_graph(db, graph)
        record = await asyncio.to_thread(knowledge.save_material, name, content, text, graph.model_dump())
    except (ValueError, RuntimeError, UnicodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'id': record['id'], 'name': record['name'], 'concepts': graph.model_dump()}


MASTERY_TARGET = .75  # A progression heuristic, not a calibrated probability.


def study_snapshot(db, session_id: int) -> dict:
    row = require_session(db, session_id)
    state = row.state
    material = knowledge.get_material(state['material_id'])
    graph = material.get('graph')
    if not graph or not graph['concepts']:
        raise HTTPException(409, 'Upload the material again to build its study graph.')
    mastery = memory.get_observer_memory(db, row.user_id)['mastery']
    return dict(type='study_state', state=public_state(state), material=material,
        graph={'nodes': [dict(id=name, label=name, mastery=mastery.get(name),
                              active=name == state.get('current_concept')) for name in graph['concepts']],
               'edges': [dict(source=e['prerequisite'], target=e['concept'])
                         for e in graph['prerequisites']]},
        mastery_target=MASTERY_TARGET)


def select_study_concept(graph: dict, mastery: dict) -> str | None:
    remaining = [n for n in graph['concepts'] if mastery.get(n, 0) < MASTERY_TARGET]
    ready = [name for name in remaining if all(
        mastery.get(e['prerequisite'], 0) >= MASTERY_TARGET
        for e in graph['prerequisites'] if e['concept'] == name)]
    if remaining and not ready:
        raise ValueError('No eligible concept; inspect the material prerequisite graph.')
    return min(ready, key=lambda name: mastery.get(name, 0)) if ready else None


async def advance_study(session_id: int) -> dict | None:
    """Called under the session job lock; event ingestion stays responsive."""
    with SessionLocal() as db:
        row = require_session(db, session_id)
        state = memory.get_short_term_memory(db, session_id)
        if state.get('ended'):
            return study_snapshot(db, session_id)
        material = knowledge.get_material(state['material_id'])
        mastery = memory.get_observer_memory(db, row.user_id)['mastery']
        concept = select_study_concept(material['graph'], mastery)
        revision = state['revision']
        recent = state.get('recent_questions', [])
    if concept is None:
        with SessionLocal.begin() as db:
            state = memory.get_short_term_memory(db, session_id)
            if state['revision'] != revision:
                return None
            memory.update_short_term_memory(db, session_id, {'study_complete': True})
            return study_snapshot(db, session_id)
    sources = await asyncio.to_thread(knowledge.retrieve_knowledge, concept, 4, state['material_id'])
    sources = sources or material['chunks'][:3]
    problem = await tools.generate_problem(concept, sources=sources, previous_prompts=recent)
    with SessionLocal.begin() as db:
        row = require_session(db, session_id)
        current = memory.get_short_term_memory(db, session_id)
        if current['revision'] != revision or current['ended']:
            return None
        temporal = {k: v for k, v in current.get('observer', {}).items()
                    if k in ('last_intervention_at', 'hint_times')}
        current.update(current_concept=concept, current_problem=problem, workspace='', plan={},
                       observer=temporal, revision=revision+1, study_complete=False,
                       recent_questions=(recent+[problem['prompt']])[-6:])
        orchestrator.persist_observation(db, row, current, 'student_action', {'action': 'problem_opened'})
        append_event(db, session_id, 'student_action',
                     {'action': 'problem_opened', 'problem_id': problem['id'], 'concept': concept})
        return study_snapshot(db, session_id)


class StudyRequest(utils.Schema):
    material_id: str = Field(min_length=1, max_length=100)


@app.post('/api/sessions/{session_id}/study')
async def start_study(session_id: int, request: StudyRequest):
    async with LOCKS.setdefault(session_id, asyncio.Lock()):
        try:
            material = await asyncio.to_thread(knowledge.get_material, request.material_id)
            if not (material.get('graph') or {}).get('concepts'):
                raise ValueError('No concepts were extracted. Upload readable learning material.')
            with SessionLocal.begin() as db:
                row = require_session(db, session_id)
                if row.state['ended']:
                    raise HTTPException(409, 'Start a new session first.')
                if row.state.get('material_id') not in (None, request.material_id):
                    raise HTTPException(409, 'Use a new session for different material.')
                if row.state.get('material_id'):
                    if row.state.get('study_complete') or (row.state['current_problem'] and
                        not row.state['current_problem'].get('solved')):
                        return study_snapshot(db, session_id)
                else:
                    memory.update_short_term_memory(db, session_id, {
                        'material_id': request.material_id, 'current_problem': {}, 'current_concept': '',
                        'workspace': '', 'observer': {}, 'plan': {}, 'revision': row.state['revision']+1})
            result = await advance_study(session_id)
            if result is None:
                raise HTTPException(409, 'Session changed during setup. Upload again to start a new session.')
            return result
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


class SkipRequest(utils.Schema):
    problem_id: str


@app.post('/api/sessions/{session_id}/skip')
async def skip_question(session_id: int, request: SkipRequest):
    async with LOCKS.setdefault(session_id, asyncio.Lock()):
        with SessionLocal() as db:
            row = require_session(db, session_id)
            if row.state.get('ended') or row.state.get('study_complete') or not row.state.get('material_id'):
                raise HTTPException(409, 'No active study question to skip.')
            if row.state['current_problem'].get('id') != request.problem_id:
                raise HTTPException(409, 'Question changed. Try again.')
        result = await advance_study(session_id)
        if result is None:
            raise HTTPException(409, 'Your work changed. Try skipping again.')
        with SessionLocal.begin() as db:
            append_event(db, session_id, 'question_skipped', {'problem_id': request.problem_id})
        await broadcast(session_id, result)
        return result


@app.get('/api/sessions/{session_id}/study')
def get_study(session_id: int):
    with SessionLocal() as db:
        row = require_session(db, session_id)
        if not row.state.get('material_id'):
            raise HTTPException(404, 'Upload material to begin.')
        return study_snapshot(db, session_id)


@app.get('/api/concepts')
def concepts():
    with SessionLocal() as db:
        return {'nodes': [{'id': c.id, 'name': c.name} for c in db.scalars(select(Concept))],
                'edges': [{'from_id': e.from_id, 'to_id': e.to_id} for e in db.scalars(select(ConceptEdge))]}


@app.get('/api/metrics')
def metrics():
    m = dict(utils.METRICS)
    m['observer_mean_latency_ms'] = m.get('observer_latency_ms', 0) / max(1, m.get('observer_events', 0))
    return m


async def broadcast(session_id: int, data: dict):
    for socket in list(SOCKETS[session_id]):
        try:
            await socket.send_json(data)
        except (WebSocketDisconnect, RuntimeError):
            SOCKETS[session_id].discard(socket)


async def run_job(job: dict):
    sid = job['session_id']
    async with LOCKS.setdefault(sid, asyncio.Lock()):
        await broadcast(sid, {'type': 'busy', 'active': True})
        try:
            outputs = await orchestrator.handle(job)
            for output in outputs:
                await broadcast(sid, output)
            assessment_output = next((o for o in outputs if o['type'] == 'assessment'), None)
            if assessment_output:
                progress = None
                with SessionLocal() as db:
                    row = db.get(Session, sid)
                    studying = bool(row.state.get('material_id'))
                    if studying:
                        progress = study_snapshot(db, sid)
                if progress:
                    await broadcast(sid, progress)
                result = assessment_output['result']
                if studying and result['correct'] and (result.get('method') == 'llm' or result['verified']):
                    following = await advance_study(sid)
                    if following:
                        await broadcast(sid, following)
                    else:
                        await broadcast(sid, {'type': 'error', 'message':
                            'Your work changed while preparing the next question. Submit your answer again when ready.'})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            utils.METRICS['job_errors'] += 1
            # API exception strings may contain request details; return a useful fixed message.
            if isinstance(exc, APIStatusError):
                status = exc.status_code
                LOG.warning('Tutor provider request failed (%s, HTTP %s)', type(exc).__name__, status)
                detail = {
                    400: 'The provider rejected the request. Check model and tool compatibility.',
                    401: 'The provider rejected the credentials. Check the configured API key.',
                    403: 'The provider denied access. Check the API key and model permissions.',
                    404: 'The requested model or endpoint was unavailable. Check the provider settings.',
                    429: 'The provider rate or quota limit was reached. Try again later.',
                }.get(status, 'The provider could not complete the request. Try again.')
                message = f'{config.LLM_PROVIDER.title()} (HTTP {status}): {detail}'
            else:
                LOG.warning('Tutor job failed (%s)', type(exc).__name__)
                if isinstance(exc, (APITimeoutError, TimeoutError)):
                    message = 'The model provider took too long to respond. Try again.'
                elif isinstance(exc, APIConnectionError):
                    message = 'The server could not connect to the model provider. Check the connection and try again.'
                else:
                    message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else 'Tutor request failed. Check the server log and retry.'
            await broadcast(sid, {'type': 'error', 'message': message[:400]})
            if job['kind'] == 'problem_attempt' or job['explicit'] or job['decision'] == 'WAKE':
                await emit_activity(sid, [dict(id=f"{job['event_id']}:failed", call_id=job['event_id'], kind='operation',
                    message='Learning call failed. No successful memory update or reflection is being reported.')])
        finally:
            with SessionLocal.begin() as db:
                orchestrator.clear_pending_wake(db, sid)
            with SessionLocal() as db:
                await broadcast(sid, {'type': 'observer_status', **observer_status(require_session(db, sid).state)})
            await broadcast(sid, {'type': 'busy', 'active': False})


async def emit_activity(sid, items):
    if items:
        await broadcast(sid, {'type': 'memory_activity', 'items': items})


@app.websocket('/ws/{session_id}')
async def websocket(socket: WebSocket, session_id: int):
    # Local-only MVP: reject browser cross-origin socket attempts.
    origin = socket.headers.get('origin')
    if origin and origin.removeprefix('http://').removeprefix('https://') != socket.headers.get('host'):
        await socket.close(code=1008)
        return
    with SessionLocal() as db:
        if db.get(Session, session_id) is None:
            await socket.close(code=1008)
            return
    await socket.accept()
    SOCKETS[session_id].add(socket)
    with SessionLocal() as db:
        await socket.send_json({'type': 'observer_status', **observer_status(require_session(db, session_id).state)})
    try:
        while True:
            raw = await socket.receive_text()
            if len(raw) > 120000:
                await socket.send_json({'type': 'error', 'message': 'Event too large.'})
                continue
            try:
                event = utils.ClientEvent.model_validate_json(raw)
                if len(TASKS[session_id]) >= 4 and (event.type in ('student_message', 'problem_attempt', 'session_end', 'feedback') or event.action == 'hint_request'):
                    raise ValueError('Please wait for queued tutor requests to finish.')
                with SessionLocal.begin() as db:
                    job = orchestrator.ingest(db, session_id, event)
                ack = {'type': 'ack', 'event_id': job['event_id'], 'event_type': job['kind'],
                       'action': event.action, 'decision': job['decision'], 'revision': job['revision']}
                if event.type == 'student_message':
                    ack['content'] = event.content
                await socket.send_json(ack)
                with SessionLocal() as db:
                    await broadcast(session_id, {'type': 'observer_status', **observer_status(require_session(db, session_id).state)})
                if job['explicit'] or job['decision'] == 'WAKE' or job['kind'] in ('problem_attempt', 'session_end', 'feedback'):
                    task = asyncio.create_task(run_job(job))
                    TASKS[session_id].add(task)
                    task.add_done_callback(TASKS[session_id].discard)
            except (ValidationError, ValueError) as exc:
                await socket.send_json({'type': 'error', 'message': str(exc)[:400]})
    except WebSocketDisconnect:
        pass
    finally:
        SOCKETS[session_id].discard(socket)
