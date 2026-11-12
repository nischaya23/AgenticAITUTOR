"""Authenticated FastAPI application; background agent jobs keep WS input live."""
import asyncio
import logging
import time
from uuid import uuid4
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from openai import APIConnectionError, APIStatusError, APITimeoutError
from pydantic import Field, ValidationError
from sqlalchemy import delete, select
from . import logging_setup as logs
from . import workflow, auth, websearch, hypothesis, plans, config, knowledge, memory, orchestrator, tools, utils, context, research, operations
from .response import Response, response_for
from .db import Session, Event, Concept, Login, Material, Operation, SessionLocal, init_db, append_event

SOCKETS: dict[int, set] = defaultdict(set)
TASKS: dict[int, set] = defaultdict(set)
LOCKS: dict[int, asyncio.Lock] = {}
LOG = logging.getLogger('tutor')
DRAFTS = None
REVIEWS = None


@asynccontextmanager
async def lifespan(app):
    global DRAFTS, REVIEWS
    logs.setup()
    init_db()
    with SessionLocal.begin() as db:
        for operation in db.scalars(select(Operation).where(Operation.status == 'running')):
            operation.status = 'interrupted'
            operation.data = {**operation.data, 'error': 'Interrupted by restart. Please retry.'}
    workflow.HEALTHY = True
    workflow.interrupt_remaining()
    dispatcher = asyncio.create_task(workflow.dispatch(broadcast))
    DRAFTS = hypothesis.DraftScheduler(SessionLocal, broadcast, track_task)
    from .observer_review import ReviewScheduler
    REVIEWS = ReviewScheduler(SessionLocal, broadcast, track_task, run_job)
    with SessionLocal.begin() as db:
        for row in db.scalars(select(Session)):
            if row.state.get('observer', {}).get('pending_wake'):
                orchestrator.clear_pending_wake(db, row.id)
            if row.state.get('assessment_pending'):
                row.state = {**row.state, 'assessment_pending': False,
                    'assessment_stages': {k: 'unavailable' if v in ('analyzing', 'waiting') else v
                        for k, v in row.state.get('assessment_stages', {}).items()}}
            state = dict(row.state)
            state['response'] = response_for(state)
            state['pending_video'] = None
            problem = dict(state.get('current_problem', {}))
            if problem.get('format') in ('diagram_construction', 'diagram_interpretation') and not problem.get('solved'):
                state['current_problem'] = {}
                state['legacy_question_replaced'] = True
            else:
                state['current_problem'] = {k:v for k,v in problem.items() if k not in ('diagram', 'starter_diagram')}
            row.state = state
            panel = row.state.get('hypothesis_state', {})
            if panel.get('status') == 'analyzing':
                row.state = {**row.state, 'hypothesis_state': {**panel, 'status': 'unavailable',
                    'error': 'Analysis interrupted. Edit your response to analyze again.'}}
    yield
    pending = [t for group in TASKS.values() for t in group] + list(operations.TASKS)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    dispatcher.cancel()
    await asyncio.gather(dispatcher, return_exceptions=True)
    SOCKETS.clear()
    TASKS.clear()
    LOCKS.clear()
    utils._PROVIDER_LIMITS.pop(asyncio.get_running_loop(), None)
    DRAFTS = None
    if REVIEWS:
        REVIEWS.cancel_all()
    from .video import _LOCKS
    _LOCKS.clear()
    logs.shutdown()


app = FastAPI(title='Agentic AI Tutor', lifespan=lifespan)
app.include_router(auth.router)
FRONTEND_BUILD = config.ROOT / 'frontend' / 'dist'
app.mount('/assets', StaticFiles(directory=FRONTEND_BUILD / 'assets', check_dir=False), name='assets')


@app.exception_handler(APIStatusError)
async def provider_http_error(request, exc):
    logs.event('http.provider_failed', provider=config.LLM_PROVIDER, status=exc.status_code)
    if exc.status_code == 429:
        detail = f'{config.LLM_PROVIDER.title()} rate or quota limit reached (HTTP 429). Please retry later.'
    elif exc.status_code >= 500:
        detail = f'{config.LLM_PROVIDER.title()} is temporarily unavailable (HTTP {exc.status_code}). Please retry shortly.'
    else:
        detail = f'{config.LLM_PROVIDER.title()} rejected the model request (HTTP {exc.status_code}). ' \
                 'Please retry. If this continues, check the model and provider settings.'
    return JSONResponse(status_code=503 if exc.status_code == 429 or exc.status_code >= 500 else 502,
        content={'detail': detail})


@app.exception_handler(APIConnectionError)
async def provider_connection_error(request, exc):
    timeout = isinstance(exc, APITimeoutError)
    logs.event('http.provider_failed', provider=config.LLM_PROVIDER, error=type(exc).__name__)
    return JSONResponse(status_code=504 if timeout else 502,
        content={'detail': 'The model provider took too long to respond. Please retry.' if timeout
                 else 'Could not connect to the model provider. Check the connection and retry.'})


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
    return {'observer_status': observer_status(state), **{k: v for k, v in state.items() if k not in ('observer', 'log_uuid', 'submitted_response')},
        'response': response_for(state), 'response_revision': state.get('response_revision', 0),
        'hypothesis_state': hypothesis.view(state),
        'current_problem': {k: v for k, v in state.get('current_problem', {}).items()
                            if k not in ('expected_answer', 'rubric', 'diagram', 'starter_diagram')}}


def require_session(db, session_id: int):
    row = db.get(Session, session_id)
    if row is None:
        raise HTTPException(404, 'Session not found.')
    return row


def session_log_uuid(session_id):
    with SessionLocal.begin() as db:
        row = require_session(db, session_id)
        if not row.state.get('log_uuid'):
            row.state = {**row.state, 'log_uuid': uuid4().hex}
        return row.state['log_uuid']


def allowed_browser_origin(origin: str | None, host: str | None) -> bool:
    return not origin or origin in config.CORS_ORIGINS or origin in (f'http://{host}', f'https://{host}')


@app.middleware('http')
async def request_logging(request, call_next):
    parts = request.url.path.split('/')
    sid = None
    if len(parts) > 3 and parts[1:3] == ['api', 'sessions']:
        try:
            sid = int(parts[3])
        except ValueError:
            pass
    if request.url.path.startswith('/api/'):
        if request.method not in ('GET', 'HEAD', 'OPTIONS'):
            origin = request.headers.get('origin')
            if not allowed_browser_origin(origin, request.headers.get('host')):
                return JSONResponse({'detail': 'Cross-origin requests are not allowed.'}, status_code=403)
        public = {'/api/health', '/api/auth/login', '/api/auth/signup', '/api/auth/me'}
        if request.url.path not in public:
            with SessionLocal() as db:
                user = auth.user_for_token(db, request.cookies.get(auth.COOKIE))
                if user is None:
                    return JSONResponse({'detail': 'Please log in.'}, status_code=401)
                request.state.user_id = user.id
                if sid is not None:
                    row = db.get(Session, sid)
                    if row is None or row.user_id != user.id:
                        return JSONResponse({'detail': 'Session not found.'}, status_code=404)
    uid = None
    if sid is not None:
        try:
            uid = session_log_uuid(sid)
        except HTTPException:
            sid = None
    with logs.scope(sid, uid):
        started = time.perf_counter()
        logs.event('http.start', method=request.method, path=request.url.path)
        try:
            if sid is not None and request.method in ('POST', 'PUT', 'PATCH', 'DELETE'):
                with workflow.run('Study setup' if parts[-1] in ('study', 'problem', 'next', 'concept', 'skip') else parts[-1].replace('_', ' ').capitalize(), session_id=sid):
                    response = await call_next(request)
                    if response.status_code >= 400:
                        workflow.outcome('failed', 'Request did not complete')
            elif request.url.path in ('/api/materials', '/api/materials/web') and request.method == 'POST':
                with workflow.run('Topic/upload preparation', user_id=request.state.user_id):
                    response = await call_next(request)
                    if response.status_code >= 400:
                        workflow.outcome('failed', 'Preparation did not complete')
            else:
                response = await call_next(request)
            if request.url.path.startswith('/api/'):
                response.headers['Cache-Control'] = 'no-store'
            logs.event('http.complete', status=response.status_code, duration_ms=round((time.perf_counter()-started)*1000,2))
            return response
        except Exception as exc:
            logs.event('http.failed', error=type(exc).__name__)
            raise


# Wrap the auth/origin middleware so preflight works without a login cookie and
# allowed browser origins can read both successful and rejected API responses.
app.add_middleware(CORSMiddleware, allow_origins=list(config.CORS_ORIGINS),
    allow_credentials=True, allow_methods=['*'], allow_headers=['*'])


@app.get('/')
def frontend():
    index = FRONTEND_BUILD / 'index.html'
    if not index.is_file():
        raise HTTPException(503, 'Build the frontend with npm ci and npm run build, or use the Vite dev server.')
    return FileResponse(index)


@app.get('/api/health')
def health():
    key = config.GEMINI_API_KEY if config.LLM_PROVIDER == 'gemini' else config.OPENAI_API_KEY
    return {'status': 'ok', 'provider': config.LLM_PROVIDER, 'model': config.GEMINI_MODEL if config.LLM_PROVIDER == 'gemini' else config.OPENAI_MODEL, 'configured': bool(key)}


@app.post('/api/sessions')
def create_session(request: Request):
    with SessionLocal.begin() as db:
        user_id = request.state.user_id
        state = utils.SessionData(log_uuid=uuid4().hex).model_dump(mode='json')
        state['assessed_hypotheses'] = memory.get_long_term_memory(db, user_id).get('hypotheses', [])
        row = Session(user_id=user_id, state=state)
        db.add(row)
        db.flush()
        with logs.scope(row.id, state['log_uuid']):
            memory.update_long_term_memory(db, user_id, {})
            append_event(db, row.id, 'session_started', {})
            logs.event('session.created')
        return {'id': row.id, 'state': public_state(state)}


@app.get('/api/sessions')
def list_sessions(request: Request):
    materials = {material['id']: material['name'] for material in knowledge.list_materials(request.state.user_id)}
    with SessionLocal() as db:
        rows = db.scalars(select(Session).where(Session.user_id == request.state.user_id)
            .order_by(Session.id.desc())).all()
        return {'sessions': [{'id': row.id, 'material_id': row.state.get('material_id'),
                             'title': materials.get(row.state.get('material_id')) or row.state.get('current_concept') or 'Study chat',
                             'created_at': row.created_at.isoformat() + ('Z' if row.created_at.tzinfo is None else ''),
                             'ended': row.state.get('ended', False),
                             'complete': row.state.get('study_complete', False)} for row in rows]}


@app.post('/api/auth/logout')
async def logout(request: Request):
    token = request.cookies.get(auth.COOKIE)
    with SessionLocal.begin() as db:
        db.execute(delete(Login).where(Login.token_hash == auth.token_hash(token)))
    for sockets in list(SOCKETS.values()):
        for socket in list(sockets):
            if socket.cookies.get(auth.COOKIE) == token:
                sockets.discard(socket)
                try:
                    await socket.close(code=1008)
                except RuntimeError:
                    pass
    response = JSONResponse({'ok': True})
    response.delete_cookie(auth.COOKIE, path='/', secure=config.COOKIE_SECURE, httponly=True, samesite='strict')
    return response


@app.get('/api/sessions/{session_id}')
def read_session(session_id: int):
    with SessionLocal() as db:
        row = require_session(db, session_id)
        history = db.scalars(select(Event).where(Event.session_id == session_id,
            Event.type.in_(['student_message', 'intervention', 'assessment', 'reflection']))
            .order_by(Event.id.desc())).all()
        links = workflow.message_links(db, session_id) or {}
        return {'id': row.id, 'state': public_state(row.state),
                'history': [{'id': e.id, 'type': e.type, 'data': e.data, 'workflow_run_id': links.get(e.id)} for e in reversed(history)]}


@app.get('/api/student')
def read_student(request: Request):
    with SessionLocal.begin() as db:
        data = memory.get_long_term_memory(db, request.state.user_id)
        return {k: v for k, v in data.items() if k != 'policy'}


class ProblemRequest(utils.Schema):
    concept: str = Field(min_length=1, max_length=200)


@app.post('/api/sessions/{session_id}/problem')
async def new_problem(session_id: int, request: ProblemRequest):
    async with LOCKS.setdefault(session_id, asyncio.Lock()):
        return await _new_problem(session_id, request)


async def _new_problem(session_id: int, request: ProblemRequest):
    with SessionLocal() as db:
        initial_row = require_session(db, session_id)
        initial = initial_row.state
        user_id = initial_row.user_id
        if initial['ended']:
            raise HTTPException(409, 'Start a new session first.')
        revision = initial['revision']
        question_context = context.for_session(db, session_id)
    try:
        sources = knowledge.retrieve_knowledge(request.concept, user_id=user_id)
        question_context.update(concept=request.concept,knowledge=sources)
        problem = await tools.generate_problem(request.concept, learning_context=question_context, sources=sources, plan=initial.get('plan', {}), plan_version=initial.get('plan_version', 0),
            hypotheses=hypothesis.eligible(initial), hypothesis_version=initial.get('hypothesis_state', {}).get('version', 0),
            recent_formats=initial.get('recent_formats', []), previous_prompts=initial.get('recent_questions', []))
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    with SessionLocal.begin() as db:
        row = require_session(db, session_id)
        state = dict(row.state)
        if state['ended'] or state['revision'] != revision:
            raise HTTPException(409, 'Work changed while generating a problem; try again.')
        state.update(current_problem=problem, current_concept=request.concept, workspace='',
                     response=Response().model_dump(),
                     response_revision=state.get('response_revision', 0)+1, assessment_pending=False,
                     plan=state.get('plan', {}) if state.get('material_id') else {},
                     revision=state['revision']+1, observer={})
        orchestrator.persist_observation(db, row, state, 'student_action', {'action': 'problem_opened'})
        append_event(db, session_id, 'student_action', {'action': 'problem_opened', 'problem_id': problem['id']})
        result = {'type': 'state', 'state': public_state(state)}
    if DRAFTS:
        DRAFTS.cancel(session_id)
    await broadcast(session_id, result)
    return result


@app.get('/api/materials')
async def materials(request: Request):
    records = await asyncio.to_thread(knowledge.list_materials, request.state.user_id)
    return [{'id': m['id'], 'name': m['name'], 'sources': m.get('sources', [m['name']]),
             'origin': m.get('origin', 'upload'), 'web_sources': m.get('web_sources', []), 'chunks': m['chunks']} for m in records]


@app.post('/api/materials')
async def upload_material(request: Request, file: UploadFile | None = File(None),
                          files: list[UploadFile] | None = File(None), web_enhance: bool = Form(False)):
    """Build one concept graph from a single file or from several selected files."""
    uploads = ([file] if file else []) + list(files or [])
    if not uploads:
        raise HTTPException(400, 'Upload at least one file.')
    if len(uploads) > config.MAX_UPLOAD_FILES:
        raise HTTPException(400, f'Upload at most {config.MAX_UPLOAD_FILES} files at once.')
    documents, total = [], 0
    try:
        for upload in uploads:
            content = await upload.read(config.MAX_UPLOAD_BYTES + 1)
            name = Path(upload.filename or 'material.txt').name
            if len(content) > config.MAX_UPLOAD_BYTES:
                raise HTTPException(413, f'{name}: the upload limit is 8 MiB per file.')
            try:
                text = await asyncio.to_thread(knowledge.extract_text, name, content)
            except (ValueError, UnicodeError) as exc:
                raise ValueError(f'{name}: {exc}' if len(uploads) > 1 else str(exc)) from exc
            total += len(text)
            if total > 600000:
                raise ValueError('Selected materials are too long together; choose fewer or smaller files.')
            documents.append((name, content, text))
        return await build_material(request.state.user_id, documents, {'web_enhance': web_enhance})
    except (ValueError, RuntimeError, UnicodeError) as exc:
        raise HTTPException(400, str(exc)) from exc


async def build_material(user_id: int, documents: list[tuple], extra: dict | None = None, focus: str | None = None, do_research=True) -> dict:
    extra = dict(extra or {})
    original = list(documents)
    with SessionLocal() as db:
        learning_context = context.preparation(db, user_id, extra.get('learning_request', focus or knowledge.material_title([d[0] for d in documents])))
    pages = []
    if do_research:
        await operations.progress('reading', message='Identifying the subject and researching gaps and prerequisites.')
        try:
            pages = await research.investigate({**learning_context,
                'uploaded_excerpts': [dict(name=d[0], text=d[2][:8000 // max(1,len(documents))]) for d in documents]},
                required=True, budget=3)
        except Exception as exc:
            logs.event('upload.research_failed', error=type(exc).__name__)
            await operations.progress('research_unavailable', message='Research unavailable; using uploaded evidence.')
        documents += [(p['title'],p['text'].encode(),p['text'],p['url']) for p in pages]
    await operations.progress('building_graph', message='Building concepts and prerequisites with source evidence.')
    graph = await knowledge.extract_concepts([(d[0],d[2]) for d in documents], focus, learning_context,
        primary_count=0 if extra.get('origin') in ('web','topic') else len(original))
    with SessionLocal.begin() as db:
        graph = knowledge.store_graph(db, graph, isolated=True)
    extra.setdefault('name', knowledge.material_title([d[0] for d in original]))
    extra.setdefault('web_enhance', False)
    extra['web_sources'] = list({p['url']:dict(title=p['title'],url=p['url']) for p in pages if p['url']}.values()) or extra.get('web_sources', [])
    extra['graph_sources'] = [dict(id=f'D{i}', title=d[0], url=d[3] if len(d)>3 else None) for i,d in enumerate(documents,1)]
    record = await asyncio.to_thread(knowledge.save_materials, documents, graph.model_dump(), extra)
    with SessionLocal.begin() as db:
        db.add(Material(id=record['id'], user_id=user_id))
    workflow.attach_material(record['id'])
    return {'id':record['id'], 'name':record['name'], 'sources':record['sources'],
        'web_enhance':record['web_enhance'], 'web_sources':record.get('web_sources', []), 'concepts':graph.model_dump()}


class WebEnhanceRequest(utils.Schema):
    enabled: bool


@app.post('/api/materials/{material_id}/web-enhance')
async def set_web_enhance(material_id: str, body: WebEnhanceRequest, request: Request):
    """Turn per-concept web lookups on or off for one of the user's materials."""
    with SessionLocal() as db:
        owned = db.get(Material, material_id)
        if owned is None or owned.user_id != request.state.user_id:
            raise HTTPException(404, 'Material not found.')
    record = await asyncio.to_thread(knowledge.update_material, material_id, web_enhance=body.enabled)
    return {'id': material_id, 'web_enhance': record['web_enhance']}


class WebTopicRequest(utils.Schema):
    topic: str = Field(min_length=2, max_length=10000)
    meaning: websearch.Meaning | None = None


@app.post('/api/materials/web')
async def web_material(request: Request, body: WebTopicRequest):
    """Study a topic without uploads. An ambiguous topic returns its meanings for the student to pick;
    otherwise the web is searched, off-topic pages are dropped, and the concept graph is built."""
    topic = ' '.join(body.topic.split())
    try:
        meaning = body.meaning
        if meaning is None:
            meanings = await websearch.interpret_topic(topic)
            if len(meanings) > 1:
                return {'type': 'choices', 'topic': topic, 'meanings': [m.model_dump() for m in meanings]}
            meaning = meanings[0]
        subject = f'{meaning.label}: {meaning.description}'
        pages = await websearch.relevant_pages(await websearch.search(meaning.query), subject)
        if not pages:
            raise ValueError(f'No web pages found about {meaning.label}. Try a more specific topic.')
        documents = [(page['title'], page['text'].encode('utf-8'), page['text'], page['url']) for page in pages]
        links = [dict(title=page['title'], url=page['url']) for page in pages if page['url']]
        links += [link for page in pages for link in page.get('links', []) if link['url']]
        return await build_material(request.state.user_id, documents,
            {'name': f'Web: {meaning.label}', 'origin': 'web', 'topic': meaning.label, 'topic_description': meaning.description,
             'web_sources': links, 'web_enhance': True, 'learning_request': topic}, focus=subject, do_research=False)
    except (ValueError, RuntimeError, UnicodeError) as exc:
        raise HTTPException(400, str(exc)) from exc


def web_query(material: dict, concept: str) -> str:
    """Add context so short concept names ("Groups") search for the right subject."""
    topic = material.get('topic')
    if topic:
        return concept if topic.lower() in concept.lower() else f'{concept} {topic}'
    edges = (material.get('graph') or {}).get('prerequisites', [])
    related = [e['prerequisite'] for e in edges if e['concept'] == concept] + [e['concept'] for e in edges if e['prerequisite'] == concept]
    return ' '.join([concept, *related[:2]])


async def study_sources(material: dict, concept: str, learning_context=None) -> list[dict]:
    """Passages for a concept. The agent searches the web once per concept when the material has
    nothing on it, or for every concept when the material has web enhancement turned on."""
    enhance = material.get('web_enhance', False)
    hits = await asyncio.to_thread(knowledge.retrieve_knowledge, concept, 8, material['id'])
    added = []
    if websearch.enabled() and concept not in material.get('web_lookups', []) and ((enhance and not any(c.get('url') for c in hits)) or not hits):
        try:
            query = web_query(material, concept)
            # Uploads have no topic, so related concepts from the map tell the filter which subject is meant.
            subject = f"{concept} (in {material['topic']}: {material.get('topic_description', '')})" if material.get('topic') \
                else f'{concept}, studied together with: {query}'
            pages = await research.investigate({**(learning_context or {}), 'subject':subject, 'suggested_query':query},required=True,budget=2)
            added = await asyncio.to_thread(knowledge.add_web_documents, material['id'], pages, concept)
            logs.event('agent.web_lookup', concept=concept, pages=len(pages), chunks=len(added))
            hits = await asyncio.to_thread(knowledge.retrieve_knowledge, concept, 8, material['id'])
        except Exception as exc:  # A failed optional lookup must never block the next question.
            logs.event('agent.web_lookup_failed', concept=concept, error=type(exc).__name__)
    if not hits:
        return added[:3] or material['chunks'][:3]
    if not enhance:
        return hits[:4]
    mixed = [c for c in hits if not c.get('url')][:2] + [c for c in hits if c.get('url')][:2]
    return mixed + [c for c in hits if c not in mixed][:4 - len(mixed)]


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
                              active=name == state.get('current_concept'),
                              available=not missing_prerequisites(graph, mastery, name)) for name in graph['concepts']],
               'edges': [dict(source=e['prerequisite'], target=e['concept'])
                         for e in graph['prerequisites']]},
        mastery_target=MASTERY_TARGET)


def missing_prerequisites(graph: dict, mastery: dict, name: str) -> list[str]:
    return [e['prerequisite'] for e in graph['prerequisites']
            if e['concept'] == name and mastery.get(e['prerequisite'], 0) < MASTERY_TARGET]


def select_study_concept(graph: dict, mastery: dict, focus: str | None = None) -> str | None:
    """The student's chosen concept until it is mastered, otherwise the weakest unlocked one."""
    remaining = [n for n in graph['concepts'] if mastery.get(n, 0) < MASTERY_TARGET]
    if focus in remaining and not missing_prerequisites(graph, mastery, focus):
        return focus
    ready = [name for name in remaining if not missing_prerequisites(graph, mastery, name)]
    if remaining and not ready:
        raise ValueError('No eligible concept; inspect the material prerequisite graph.')
    return min(ready, key=lambda name: mastery.get(name, 0)) if ready else None


async def advance_study(session_id: int, chosen: str | None = None) -> dict | None:
    """Called under the session job lock; event ingestion stays responsive. `chosen` is a concept the
    student picked (already checked as unlocked); it stays the focus until mastered."""
    await plans.ensure(session_id, SessionLocal)
    with SessionLocal() as db:
        row = require_session(db, session_id)
        state = memory.get_short_term_memory(db, session_id)
        if state.get('ended'):
            return study_snapshot(db, session_id)
        material = knowledge.get_material(state['material_id'])
        question_context = context.for_session(db, session_id)
        mastery = memory.get_observer_memory(db, row.user_id)['mastery']
        concept = chosen or select_study_concept(material['graph'], mastery, state.get('focus_concept'))
        focus = concept if concept in (chosen, state.get('focus_concept')) else None
        revision = state['revision']
        recent = state.get('recent_questions', [])
        eligible = [name for name in material['graph']['concepts'] if not missing_prerequisites(material['graph'], mastery, name)]
    if concept is None:
        with SessionLocal.begin() as db:
            state = memory.get_short_term_memory(db, session_id)
            if state['revision'] != revision:
                return None
            memory.update_short_term_memory(db, session_id, {'study_complete': True})
            return study_snapshot(db, session_id)
    question_context['concept']=concept
    sources = await study_sources(material, concept, question_context)
    question_context.update(knowledge=sources,previous_problem=question_context.pop('problem',{}),previous_response=question_context.pop('response',{}))
    question_context.pop('workspace',None)
    problem = await tools.generate_problem(concept, learning_context=question_context, sources=sources, previous_prompts=recent, plan=state.get('plan', {}), plan_version=state.get('plan_version', 0),
        hypotheses=hypothesis.eligible(state), hypothesis_version=state.get('hypothesis_state', {}).get('version', 0),
        eligible_concepts=eligible, recent_formats=state.get('recent_formats', []))
    with SessionLocal.begin() as db:
        row = require_session(db, session_id)
        current = memory.get_short_term_memory(db, session_id)
        if current['revision'] != revision or current['ended'] or current.get('plan_version', 0) != state.get('plan_version', 0):
            return None
        temporal = {k: v for k, v in current.get('observer', {}).items()
                    if k in ('last_intervention_at', 'hint_times')}
        current.update(current_concept=concept, current_problem=problem, workspace='',
                       response=Response().model_dump(),
                       response_revision=current.get('response_revision', 0)+1, assessment_pending=False,
                       question_generation_error=None, recent_formats=(current.get('recent_formats', [])+[problem.get('format', 'explanation')])[-6:],
                       observer=temporal, revision=revision+1, study_complete=False, focus_concept=focus,
                       recent_questions=(recent+[problem['prompt']])[-6:])
        orchestrator.persist_observation(db, row, current, 'student_action', {'action': 'problem_opened'})
        append_event(db, session_id, 'student_action',
                     {'action': 'problem_opened', 'problem_id': problem['id'], 'concept': concept})
        if DRAFTS:
            DRAFTS.cancel(session_id)
        return study_snapshot(db, session_id)


class StudyRequest(utils.Schema):
    material_id: str = Field(min_length=1, max_length=100)


@app.post('/api/sessions/{session_id}/study')
async def start_study(session_id: int, request: StudyRequest, http_request: Request):
    async with LOCKS.setdefault(session_id, asyncio.Lock()):
        try:
            with SessionLocal() as db:
                owned = db.get(Material, request.material_id)
                if owned is None or owned.user_id != http_request.state.user_id:
                    raise HTTPException(404, 'Material not found.')
            material = await asyncio.to_thread(knowledge.get_material, request.material_id)
            if not (material.get('graph') or {}).get('concepts'):
                raise ValueError('No concepts were extracted. Upload readable learning material.')
            with SessionLocal.begin() as db:
                row = require_session(db, session_id)
                if row.state['ended']:
                    raise HTTPException(409, 'Start a new session first.')
                if row.state.get('material_id') not in (None, request.material_id):
                    raise HTTPException(409, 'Use a new session for different material.')
                if not row.state.get('material_id'):
                    memory.update_short_term_memory(db, session_id, {
                        'material_id': request.material_id, 'current_problem': {}, 'current_concept': '',
                        'workspace': '', 'observer': {}, 'plan': {}, 'revision': row.state['revision']+1})
            await plans.ensure(session_id, SessionLocal)
            with SessionLocal() as db:
                state = require_session(db, session_id).state
                if state.get('study_complete') or (state.get('current_problem') and not state['current_problem'].get('solved')):
                    return study_snapshot(db, session_id)
            result = await advance_study(session_id)
            if result is None:
                raise HTTPException(409, 'Session changed during setup. Upload again to start a new session.')
            return result
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc


@app.put('/api/sessions/{session_id}/plan')
async def edit_plan(session_id: int, request: plans.Update):
    async with LOCKS.setdefault(session_id, asyncio.Lock()):
        with SessionLocal.begin() as db:
            row = require_session(db, session_id)
            state = row.state
            if state['ended'] or not state.get('material_id'):
                raise HTTPException(409, 'No active material to plan.')
            if state.get('plan_version', 0) != request.expected_version:
                raise HTTPException(409, 'Plan changed. Review the latest saved plan and retry.')
            plan = request.plan.model_dump()
            memory.update_short_term_memory(db, session_id, dict(plan=plan,
                plan_version=request.expected_version+1, plan_suggestion=None))
            append_event(db, session_id, 'plan_edited', {'plan': plan, 'plan_version': request.expected_version+1})
            result = plans.view(row.state)
        logs.event('plan.edited', plan_version=result['plan_version'])
        await broadcast(session_id, result)
        return result


@app.post('/api/sessions/{session_id}/plan/suggestion')
async def act_on_plan_suggestion(session_id: int, request: plans.SuggestionAction):
    async with LOCKS.setdefault(session_id, asyncio.Lock()):
        with SessionLocal.begin() as db:
            row = require_session(db, session_id)
            state = row.state
            suggestion = state.get('plan_suggestion')
            if state['ended'] or not state.get('material_id'):
                raise HTTPException(409, 'No active material to plan.')
            if (state.get('plan_version', 0) != request.expected_version or not suggestion
                    or suggestion['id'] != request.suggestion_id or suggestion['base_version'] != request.expected_version):
                raise HTTPException(409, 'Suggestion changed. Review the latest plan.')
            plan = suggestion['plan'] if request.action == 'accept' else state['plan']
            memory.update_short_term_memory(db, session_id, dict(plan=plan,
                plan_version=request.expected_version+1, plan_suggestion=None))
            append_event(db, session_id, 'plan_suggestion_'+request.action, {'suggestion_id': suggestion['id'], 'plan_version': request.expected_version+1})
            result = plans.view(row.state)
        logs.event('plan.suggestion.'+request.action, plan_version=result['plan_version'])
        await broadcast(session_id, result)
        return result


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


class ConceptRequest(utils.Schema):
    concept: str = Field(min_length=1, max_length=200)


@app.post('/api/sessions/{session_id}/concept')
async def choose_concept(session_id: int, request: ConceptRequest):
    """Switch to a concept from the map once its prerequisites are mastered."""
    async with LOCKS.setdefault(session_id, asyncio.Lock()):
        with SessionLocal() as db:
            row = require_session(db, session_id)
            state = row.state
            if state.get('ended') or not state.get('material_id'):
                raise HTTPException(409, 'No active study session.')
            if state.get('assessment_pending'):
                raise HTTPException(409, 'Wait for your current answer to be assessed.')
            graph = knowledge.get_material(state['material_id'])['graph']
            if request.concept not in graph['concepts']:
                raise HTTPException(404, 'That concept is not in this learning map.')
            missing = missing_prerequisites(graph, memory.get_observer_memory(db, row.user_id)['mastery'], request.concept)
            if missing:
                raise HTTPException(409, 'Master the prerequisites first: ' + ', '.join(missing) + '.')
        result = await advance_study(session_id, chosen=request.concept)
        if result is None:
            raise HTTPException(409, 'Your work changed. Try switching again.')
        with SessionLocal.begin() as db:
            append_event(db, session_id, 'concept_selected', {'concept': request.concept})
        await broadcast(session_id, result)
        return result


@app.post('/api/sessions/{session_id}/next')
async def retry_next_question(session_id: int, request: SkipRequest):
    async with LOCKS.setdefault(session_id, asyncio.Lock()):
        with SessionLocal() as db:
            state = require_session(db, session_id).state
            if state.get('ended') or not state.get('material_id') or not state['current_problem'].get('solved') or state['current_problem'].get('id') != request.problem_id:
                raise HTTPException(409, 'No assessed question awaiting progression.')
        try:
            result = await advance_study(session_id)
        except Exception as exc:
            raise HTTPException(503, 'Next question unavailable. Your assessment is saved; retry when ready.') from exc
        if result is None:
            raise HTTPException(409, 'Work changed while preparing the question. Retry when ready.')
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
def concepts(request: Request):
    materials = knowledge.list_materials(request.state.user_id)
    graphs = [material.get('graph') or {} for material in materials]
    names = {name for graph in graphs for name in graph.get('concepts', [])}
    edges = {(edge['prerequisite'], edge['concept']) for graph in graphs for edge in graph.get('prerequisites', [])}
    with SessionLocal() as db:
        nodes = list(db.scalars(select(Concept).where(Concept.name.in_(names))))
        ids = {node.name: node.id for node in nodes}
        return {'nodes': [{'id': node.id, 'name': node.name} for node in nodes],
                'edges': [{'from_id': ids[a], 'to_id': ids[b]} for a, b in edges if a in ids and b in ids]}


@app.get('/api/metrics')
def metrics():
    m = dict(utils.METRICS)
    m['observer_mean_latency_ms'] = m.get('observer_latency_ms', 0) / max(1, m.get('observer_events', 0))
    return m


async def broadcast(session_id: int, data: dict):
    for socket in list(SOCKETS[session_id]):
        if data.get('type') in ('workflow_events', 'workflow_tracking') and socket.query_params.get('workflow') != '1':
            continue
        try:
            with SessionLocal() as db:
                valid = auth.user_for_token(db, socket.cookies.get(auth.COOKIE)) is not None
            if not valid:
                SOCKETS[session_id].discard(socket)
                await socket.close(code=1008)
                continue
            await socket.send_json(data)
        except (WebSocketDisconnect, RuntimeError):
            SOCKETS[session_id].discard(socket)


def track_task(sid, task):
    TASKS[sid].add(task)
    task.add_done_callback(TASKS[sid].discard)


async def _run_job(job: dict):
    sid = job['session_id']
    async with LOCKS.setdefault(sid, asyncio.Lock()):
        workflow.start_run(workflow.CURRENT.get())
        workflow.record('execution.started', message='Teaching/grading slot acquired', queue_ms=round((utils.now()-job.get('received_at', utils.now()))*1000, 2))
        await broadcast(sid, {'type': 'busy', 'active': True})
        try:
            async def research_progress(kind, **data):
                await broadcast(sid, {'type':'research_progress','kind':kind,**data})
            token = operations.EMITTER.set(research_progress)
            try:
                outputs = await orchestrator.handle(job)
            finally:
                operations.EMITTER.reset(token)
            for output in outputs:
                output['workflow_run_id'] = workflow.CURRENT.get().run_id if workflow.CURRENT.get() else None
                await broadcast(sid, output)
                workflow.record('delivery.'+output['type'], message='Output delivered', event_id=output.get('event_id'))
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
                    try:
                        following = await advance_study(sid)
                    except Exception as exc:
                        logs.event('question.failed', error=type(exc).__name__)
                        following = None
                    if following:
                        await broadcast(sid, following)
                    else:
                        with SessionLocal.begin() as db:
                            memory.update_short_term_memory(db, sid, {'question_generation_error':
                                'Next question unavailable. Your assessment is saved; retry when ready.'})
                            progress = study_snapshot(db, sid)
                        await broadcast(sid, progress)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            workflow.outcome('failed', 'Tutor request failed')
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
                if job['kind'] == 'problem_attempt' and require_session(db, sid).state.get('pending_assessment_event_id') == job['event_id']:
                    memory.update_short_term_memory(db, sid, {'assessment_pending': False})
            with SessionLocal() as db:
                await broadcast(sid, {'type': 'observer_status', **observer_status(require_session(db, sid).state)})
                state = require_session(db, sid).state
                if DRAFTS and job['kind'] == 'problem_attempt' and state.get('response_revision', 0) != job.get('response_revision'):
                    DRAFTS.schedule(sid)
            await broadcast(sid, {'type': 'busy', 'active': False})
            await memory_changed(sid)
            if REVIEWS and job['kind'] == 'problem_attempt':
                REVIEWS.schedule(sid, job, priority=True)


async def run_job(job):
    with workflow.run(job['kind'].replace('_', ' ').capitalize(), session_id=job['session_id'], trace=workflow.load_trace(job.get('workflow_run_id')), independent=True, queued=True, trigger={'parent_run_id': job.get('parent_run_id'), 'event_id': job['event_id']}), logs.scope(job['session_id'], session_log_uuid(job['session_id']), trigger_event_id=job['event_id']):
        started = time.perf_counter()
        logs.event('job.start', kind=job['kind'])
        await _run_job(job)
        logs.event('job.finished', kind=job['kind'], duration_ms=round((time.perf_counter()-started)*1000,2))


async def emit_activity(sid, items):
    if items:
        await broadcast(sid, {'type': 'memory_activity', 'items': items})


@app.websocket('/ws/{session_id}')
async def websocket(socket: WebSocket, session_id: int):
    # Use the same trusted dev origins for HTTP and authenticated sockets.
    origin = socket.headers.get('origin')
    if not allowed_browser_origin(origin, socket.headers.get('host')):
        await socket.close(code=1008)
        return
    with SessionLocal() as db:
        user = auth.user_for_token(db, socket.cookies.get(auth.COOKIE))
        row = db.get(Session, session_id)
        if user is None or row is None or row.user_id != user.id:
            await socket.close(code=1008)
            return
    await socket.accept()
    SOCKETS[session_id].add(socket)
    if not workflow.HEALTHY and socket.query_params.get('workflow') == '1':
        await socket.send_json({'type': 'workflow_tracking', 'tracking': 'incomplete'})
    with SessionLocal() as db:
        await socket.send_json({'type': 'observer_status', **observer_status(require_session(db, session_id).state)})
    try:
        while True:
            raw = await socket.receive_text()
            with SessionLocal() as db:
                if auth.user_for_token(db, socket.cookies.get(auth.COOKIE)) is None:
                    await socket.close(code=1008)
                    return
            if len(raw) > 120000:
                await socket.send_json({'type': 'error', 'message': 'Event too large.'})
                continue
            try:
                event = utils.ClientEvent.model_validate_json(raw)
                if len(TASKS[session_id]) >= 4 and (event.type in ('student_message', 'problem_attempt', 'session_end', 'feedback') or event.action == 'hint_request'):
                    raise ValueError('Please wait for queued tutor requests to finish.')
                with logs.scope(session_id, session_log_uuid(session_id)):
                    logs.event('socket.event', kind=event.type, action=event.action)
                    with SessionLocal.begin() as db:
                        job = orchestrator.ingest(db, session_id, event)
                    if DRAFTS and event.type in ('problem_attempt', 'session_end'):
                        DRAFTS.cancel(session_id)
                    elif DRAFTS and job.get('draft_changed') and event.action in ('edit', 'revert'):
                        DRAFTS.schedule(session_id)
                    logs.event('observer.decision', decision=job['decision'], event_id=job['event_id'])
                ack = {'type': 'ack', 'event_id': job['event_id'], 'event_type': job['kind'], 'action': event.action, 'decision': job['decision'], 'revision': job['revision']}
                ack['response_revision'] = job['response_revision']
                ack['workflow_run_id'] = job.get('workflow_run_id')
                if event.type == 'student_message':
                    ack['content'] = event.content
                await socket.send_json(ack)
                with SessionLocal() as db:
                    await broadcast(session_id, {'type': 'observer_status', **observer_status(require_session(db, session_id).state)})
                if REVIEWS and not job['explicit'] and job['kind'] not in ('problem_attempt', 'feedback', 'session_end'):
                    REVIEWS.schedule(session_id, job)
                if job['kind'] == 'session_end':
                    from .features import cancel_videos
                    cancel_videos(session_id)
                    if REVIEWS: REVIEWS.cancel(session_id)
                if job['explicit'] or (not config.OBSERVER_LLM_ENABLED and job['decision'] == 'WAKE') or job['kind'] in ('problem_attempt', 'session_end', 'feedback'):
                    task = asyncio.create_task(run_job(job))
                    track_task(session_id, task)
            except (ValidationError, ValueError) as exc:
                await socket.send_json({'type': 'error', 'message': str(exc)[:400]})
    except WebSocketDisconnect:
        pass
    finally:
        SOCKETS[session_id].discard(socket)


async def memory_changed(session_id):
    with SessionLocal() as db:
        row = db.get(Session, session_id)
        if row is None:
            return
        ids = list(db.scalars(select(Session.id).where(Session.user_id == row.user_id)))
    for sid in ids:
        await broadcast(sid, {'type':'memory_changed'})

from .workflow_api import router as workflow_router
app.include_router(workflow_router)

from .features import router as feature_router
app.include_router(feature_router)
