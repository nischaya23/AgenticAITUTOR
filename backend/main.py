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
from pydantic import Field, ValidationError
from sqlalchemy import select
from . import config, knowledge, memory, observer, orchestrator, tools, utils
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
app.mount('/static', StaticFiles(directory=config.ROOT / 'frontend'), name='static')


def public_state(state: dict) -> dict:
    return {**{k: v for k, v in state.items() if k != 'observer'},
        'current_problem': {k: v for k, v in state.get('current_problem', {}).items()
                            if k not in ('expected_answer', 'rubric')}}


def require_session(db, session_id: int):
    row = db.get(Session, session_id)
    if row is None:
        raise HTTPException(404, 'Session not found.')
    return row


@app.get('/')
def frontend():
    return FileResponse(config.ROOT / 'frontend' / 'index.html')


@app.get('/api/health')
def health():
    return {'status': 'ok', 'demo_mode': config.DEMO_MODE, 'configured': bool(config.OPENAI_API_KEY)}


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
        record = await asyncio.to_thread(knowledge.save_material, name, content, text)
        with SessionLocal.begin() as db:
            knowledge.store_graph(db, graph)
    except (ValueError, RuntimeError, UnicodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'id': record['id'], 'name': record['name'], 'concepts': graph.model_dump()}


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
            for output in await orchestrator.handle(job):
                await broadcast(sid, output)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            utils.METRICS['job_errors'] += 1
            LOG.warning('Tutor job failed (%s)', type(exc).__name__)
            # API exception strings may contain request details; return a useful fixed message.
            message = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else 'Tutor request failed. Check your API key, model, or server log and retry.'
            await broadcast(sid, {'type': 'error', 'message': message[:400]})
        finally:
            with SessionLocal.begin() as db:
                orchestrator.clear_pending_wake(db, sid)
            await broadcast(sid, {'type': 'busy', 'active': False})


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
                await socket.send_json({'type': 'ack', 'event_id': job['event_id'],
                                        'decision': job['decision'], 'revision': job['revision']})
                if job['explicit'] or job['decision'] == 'WAKE' or job['kind'] in ('problem_attempt', 'session_end') or job['replan']:
                    task = asyncio.create_task(run_job(job))
                    TASKS[session_id].add(task)
                    task.add_done_callback(TASKS[session_id].discard)
            except (ValidationError, ValueError) as exc:
                await socket.send_json({'type': 'error', 'message': str(exc)[:400]})
    except WebSocketDisconnect:
        pass
    finally:
        SOCKETS[session_id].discard(socket)
