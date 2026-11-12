"""Local, fail-open workflow evidence. Context is immutable and local to each task.

Events and memory changes join the caller's transaction. Model/render spans use
short independent transactions and never hold SQLite locks across an await.
The ordered dispatcher reads only committed events; inspection never traces.
"""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from functools import wraps
import hashlib
import inspect
import json
import logging
import re
from uuid import uuid4
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.sqlite import insert
from . import db as database
from .db import (WorkflowRun as Run, WorkflowSpan as Span, WorkflowEvent as Event,
                 WorkflowArtifact as Artifact, Session, utcnow)

CURRENT = ContextVar('workflow_context', default=None)
PREVIOUS = ContextVar('workflow_previous_span', default=None)
PENDING_READS = ContextVar('workflow_pending_reads', default=())
PREVIOUS_INPUT = ContextVar('workflow_previous_input', default=None)
ACTIVE = ('queued', 'running', 'waiting')
TERMINAL = ('completed', 'warnings', 'failed', 'cancelled', 'discarded', 'interrupted')
LOG = logging.getLogger('tutor.workflow')
HEALTHY = True
# Never persist provider envelopes, system prompts, grading references or hidden reasoning.
PRIVATE = {'expected_answer', 'rubric', 'reference_answer', 'reference_answers',
           'api_key', 'authorization', 'password', 'password_hash', 'credentials',
           'provider_call', 'provider_signature', 'thought_signature', 'signature',
           'reasoning', 'chain_of_thought', 'hidden_reasoning', 'instructions', 'policy',
           'retry_data', 'submitted_response', 'log_uuid'}


def public(value, depth=0):
    if depth > 22:
        return '[Depth budget omitted]'
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    if isinstance(value, dict):
        return {str(k): public(v, depth+1) for k, v in value.items()
                if str(k).lower() not in PRIVATE and not any(s in str(k).lower() for s in ('secret', 'api_key', 'signature', 'access_token'))}
    if isinstance(value, (list, tuple)):
        return [public(v, depth+1) for v in value[:100]]
    if isinstance(value, str):
        if value.lstrip().startswith(('{', '[')):
            try:
                return public(json.loads(value), depth+1)
            except (ValueError, TypeError):
                pass
        return re.sub(r'\b(?:sk-[A-Za-z0-9_-]{12,}|AIza[A-Za-z0-9_-]{20,})\b', '[redacted]', value[:64000])
    if value is None or isinstance(value, (bool, float, int)):
        return value
    return '[Non-public provider object]'


def bounded(value):
    data = public(value)
    if len(json.dumps(data, ensure_ascii=False)) > 120000:
        return {'tracking_omission': 'Public evidence exceeded the storage budget.',
                'snapshot': data.get('snapshot', {}) if isinstance(data, dict) else {}}
    return data


def safe(fn):
    @wraps(fn)
    def guarded(*args, **kwargs):
        global HEALTHY
        try:
            return fn(*args, **kwargs)
        except Exception as exc:
            HEALTHY = False
            LOG.warning('Workflow tracking incomplete (%s)', type(exc).__name__)
            return None
    return guarded


@dataclass(frozen=True)
class Trace:
    run_id: str
    user_id: int
    session_id: int | None = None
    operation_id: str | None = None
    span_id: str | None = None


@contextmanager
def transaction(db=None):
    if db is not None:
        yield db
    else:
        with database.SessionLocal.begin() as own:
            yield own


def _event(db, trace, kind, category, status, data, span_id=None):
    entry = Event(id=uuid4().hex, run_id=trace.run_id, span_id=span_id or trace.span_id,
        user_id=trace.user_id, session_id=trace.session_id, operation_id=trace.operation_id,
        type=kind, category=category, status=status, data=bounded(data))
    db.add(entry)
    db.flush()
    return entry


@safe
def record(kind, category='agents', status='completed', db=None, **data):
    trace = CURRENT.get()
    if trace:
        with transaction(db) as conn:
            if trace.span_id and kind in ('model.usage', 'model.slot_acquired'):
                current_span = conn.get(Span, trace.span_id)
                if current_span:
                    current_span.data = {**current_span.data, **bounded(data)}
            if status == 'warnings':
                current_run = conn.get(Run, trace.run_id)
                if current_run:
                    current_run.data = {**current_run.data, 'warnings': True}
            return _event(conn, trace, kind, category, status, data).id


@safe
def create_run(user_id, name, session_id=None, operation_id=None, trigger=None, db=None):
    rid = uuid4().hex
    trace = Trace(rid, user_id, session_id, operation_id)
    with transaction(db) as conn:
        conn.add(Run(id=rid, user_id=user_id, session_id=session_id, operation_id=operation_id,
                     name=name, status='queued', data=bounded(trigger or {})))
        conn.flush()
        _event(conn, trace, 'run.queued', 'agents', 'queued', {'name': name})
    return trace


@safe
def load_trace(run_id):
    with database.SessionLocal() as conn:
        row = conn.get(Run, run_id)
        return Trace(row.id, row.user_id, row.session_id, row.operation_id) if row else None


@safe
def update_run_data(db=None, **data):
    trace = CURRENT.get()
    if trace:
        with transaction(db) as conn:
            row = conn.get(Run, trace.run_id)
            row.data = {**row.data, **bounded(data)}


@safe
def finish(status='completed', trace=None, db=None):
    trace = trace or CURRENT.get()
    if not trace:
        return
    with transaction(db) as conn:
        flush_reads(conn)
        row = conn.get(Run, trace.run_id)
        if row is None or row.status in TERMINAL:
            return
        if status == 'completed':
            problems = conn.scalars(select(Span.status).where(Span.run_id == row.id,
                Span.status.in_(['failed', 'warnings']))).all()
            if problems or row.data.get('warnings'):
                status = 'warnings'
        row.status, row.ended_at = status, utcnow()
        for work in conn.scalars(select(Span).where(Span.run_id == row.id, Span.status.in_(ACTIVE))):
            work.status, work.ended_at = status, row.ended_at
            work.data = {**work.data, 'duration_ms': elapsed(work.started_at, work.ended_at)}
        _event(conn, trace, 'run.'+status, 'errors' if status in ('failed', 'warnings') else 'agents', status, {'name': row.name})


@safe
def outcome(status, reason, db=None):
    record('outcome', status=status, db=db, reason=reason)
    finish(status, db=db)


@safe
def session_owner(session_id):
    with database.SessionLocal() as conn:
        row = conn.get(Session, session_id)
        return row.user_id if row else None


@contextmanager
def run(name, *, session_id=None, user_id=None, operation_id=None, trigger=None, trace=None, independent=False, queued=False):
    """Independent workers always start new runs, even when create_task copied context."""
    previous = CURRENT.get()
    if previous and not independent and trace is None:
        yield previous
        return
    if trace is None:
        if user_id is None:
            user_id = session_owner(session_id)
        if user_id is not None:
            trace = create_run(user_id, name, session_id, operation_id, trigger)
    token = CURRENT.set(trace)
    prior = PREVIOUS.set(None)
    pending = PENDING_READS.set(())
    previous_input = PREVIOUS_INPUT.set(None)
    try:
        if not queued:
            start_run(trace)
        yield trace
    except asyncio.CancelledError:
        finish('cancelled')
        raise
    except BaseException:
        finish('failed')
        raise
    else:
        finish()
    finally:
        PREVIOUS_INPUT.reset(previous_input)
        PENDING_READS.reset(pending)
        PREVIOUS.reset(prior)
        CURRENT.reset(token)


@safe
def start_run(trace):
    if trace:
        with transaction() as conn:
            row = conn.get(Run, trace.run_id)
            if row is None or row.status in TERMINAL:
                return
            row.status = 'running'
            root_id = row.data.get('root_span_id') or uuid4().hex
            if not row.data.get('root_span_id'):
                conn.add(Span(id=root_id, run_id=row.id, name=row.name, category='agents',
                    status='running', parent_id=None, dependencies=[], consumed=[], produced=[],
                    data={'queue_ms': elapsed(row.started_at, utcnow())}))
                row.data = {**row.data, 'root_span_id': root_id}
            CURRENT.set(replace(trace, span_id=root_id))
            _event(conn, CURRENT.get(), 'run.running', 'agents', 'running', {'name': row.name})


@safe
def artifact(kind, value, relation='consumed', db=None):
    trace = CURRENT.get()
    if not trace:
        return None
    content = bounded(value)
    if not isinstance(content, dict):
        content = {'value': content}
    digest = hashlib.sha256(json.dumps([trace.user_id, kind, content], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with transaction(db) as conn:
        conn.execute(insert(Artifact).values(id=digest, user_id=trace.user_id, kind=kind,
            data=content, created_at=utcnow()).on_conflict_do_nothing(index_elements=['id']))
        if trace.span_id:
            span = conn.get(Span, trace.span_id)
            values = getattr(span, relation)
            if digest not in values:
                setattr(span, relation, values+[digest])
        _event(conn, trace, 'artifact.'+relation, 'context', 'completed', {'artifact_id': digest, 'kind': kind})
    return digest


@safe
def version_artifact(kind, value, version, relation='consumed', db=None):
    trace = CURRENT.get()
    if not trace:
        return
    aid = artifact(kind+'_version', {'value': value, 'version': version}, relation, db=db)
    if not aid or relation != 'consumed':
        return aid
    with transaction(db) as conn:
        # Match the exact immutable value and version, never the last completion.
        prior = conn.scalars(select(Event).where(Event.user_id == trace.user_id,
            Event.type == 'artifact.produced', Event.data['artifact_id'].as_string() == aid,
            Event.run_id != trace.run_id).order_by(Event.cursor.desc()).limit(1)).first()
        if prior:
            _event(conn, trace, 'context.dependency', 'context', 'completed',
                {'artifact_id': aid, 'source_run_id': prior.run_id, 'source_span_id': prior.span_id,
                 'version': version, 'kind': kind})
            if trace.span_id and prior.span_id:
                row = conn.get(Span, trace.span_id)
                row.dependencies = list(dict.fromkeys(row.dependencies+[prior.span_id]))
    return aid


@contextmanager
def span(name, category='agents', dependencies=None, **metadata):
    trace = CURRENT.get()
    if not trace:
        yield None
        return
    sid = uuid4().hex
    deps = list(dict.fromkeys([d for d in [PREVIOUS.get(), *(dependencies or [])] if d and d != trace.span_id]))
    @safe
    def start():
        flush_reads()
        with transaction() as conn:
            conn.add(Span(id=sid, run_id=trace.run_id, parent_id=trace.span_id, name=name,
                category=category, status='running', dependencies=deps, data=bounded(metadata)))
            conn.flush()
            _event(conn, trace, 'span.started', category, 'running', {'name': name, **metadata}, sid)
    @safe
    def end(status):
        with transaction() as conn:
            row = conn.get(Span, sid)
            if not row:
                return
            row.status, row.ended_at = status, utcnow()
            row.data = {**row.data, 'duration_ms': elapsed(row.started_at, row.ended_at)}
            _event(conn, trace, 'span.'+status, category, status, {'name': name, **row.data}, sid)
    start()
    token = CURRENT.set(replace(trace, span_id=sid))
    prior = PREVIOUS.set(None)
    try:
        yield sid
    except asyncio.CancelledError:
        end('cancelled')
        raise
    except BaseException as exc:
        record('span.error', 'errors', 'failed', error=type(exc).__name__)
        end('failed')
        raise
    else:
        end('completed')
    finally:
        PREVIOUS.reset(prior)
        CURRENT.reset(token)
        PREVIOUS.set(sid)


@contextmanager
def local_span(db, name, category='memory'):
    """Synchronous stages join the caller's transaction instead of opening a writer."""
    trace = CURRENT.get()
    if not trace:
        yield
        return
    sid = uuid4().hex
    @safe
    def start():
        db.add(Span(id=sid, run_id=trace.run_id, parent_id=trace.span_id, name=name,
            category=category, status='running', dependencies=[], consumed=[], produced=[], data={}))
        db.flush()
        _event(db, trace, 'span.started', category, 'running', {'name': name}, sid)
    @safe
    def end(status):
        row = db.get(Span, sid)
        if row:
            row.status, row.ended_at = status, utcnow()
            row.data = {'duration_ms': elapsed(row.started_at, row.ended_at)}
            _event(db, trace, 'span.'+status, category, status, {'name': name}, sid)
    start()
    token = CURRENT.set(replace(trace, span_id=sid))
    try:
        yield
    except BaseException:
        end('failed')
        raise
    else:
        end('completed')
    finally:
        CURRENT.reset(token)


def elapsed(start, end):
    return round((end.replace(tzinfo=None)-start.replace(tzinfo=None)).total_seconds()*1000, 2)


def stage(name, category='agents', capture=True):
    """Agent/tool entry points; no extra model requests or scheduling changes."""
    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            with span(name, category):
                if capture:
                    values = dict(inspect.signature(fn).bind(*args, **kwargs).arguments)
                    artifact('stage_input', values)
                result = await fn(*args, **kwargs)
                if capture and result is not None:
                    artifact('stage_output', result, 'produced')
                return result
        return wrapped
    return decorate


@safe
def model_context(value, history=None):
    # Called at the provider boundary AFTER compaction, before serialization.
    aid = artifact('model_input', {'input': value, 'tool_history': history or [],
        'public_projection': True, 'protected_fields_omitted': sorted(PRIVATE)})
    projected = bounded(value)
    def flatten(v, prefix=''):
        if isinstance(v, dict):
            return {key: item for k, child in v.items() for key, item in flatten(child, prefix+str(k)+'.').items()}
        return {prefix.rstrip('.'): v}
    current = flatten(projected)
    previous = PREVIOUS_INPUT.get()
    if previous:
        prior_id, prior = previous
        artifact('context_changes', {'previous_input_artifact_id': prior_id, 'current_input_artifact_id': aid,
            'added_fields': sorted(set(current)-set(prior)), 'removed_fields': sorted(set(prior)-set(current)),
            'changed_fields': sorted(k for k in current.keys() & prior.keys() if current[k] != prior[k])})
    PREVIOUS_INPUT.set((aid, current))
    contexts = []
    documents = []
    def visit(v):
        if isinstance(v, dict):
            for field in ('sources', 'documents', 'uploaded_excerpts', 'pages'):
                if isinstance(v.get(field), list) and v[field]:
                    documents.append({'field': field, 'passages': v[field]})
            if 'snapshot' in v:
                contexts.append(v)
            for child in v.values():
                visit(child)
        elif isinstance(v, list):
            for child in v:
                visit(child)
    visit(value)
    if documents:
        artifact('included_documents', {'inputs': documents})
    for ctx in contexts:
        artifact('context_versions', {k: ctx[k] for k in ('snapshot', 'plan_version', 'hypothesis_version', 'context_budget') if k in ctx})
        if ctx.get('hypotheses'):
            version_artifact('hypotheses', ctx['hypotheses'], ctx['snapshot'].get('hypothesis_version', 0))
        version_artifact('plan', ctx.get('plan', {}), ctx.get('plan_version', 0))
        for field in ('plan', 'student', 'hypotheses', 'knowledge'):
            if field in ctx:
                artifact('included_'+field, {'value': ctx[field], 'snapshot': ctx['snapshot']})
    record('context.received', 'context', message='Public projection of the compacted model input',
           included_sources=[p.get('id') for ctx in contexts for p in ctx.get('knowledge', [])])


@safe
def retrieved(passages):
    if CURRENT.get():
        PENDING_READS.set(PENDING_READS.get()+((CURRENT.get(), 'retrieved_passages', bounded({'passages': passages}), {'source_ids': [p['id'] for p in passages]}),))


@safe
def memory_read(db, kind, value, **versions):
    # Defer reads until outside the caller's read transaction, which may close
    # without commit. This also avoids acquiring a write lock during inspection.
    if CURRENT.get():
        PENDING_READS.set(PENDING_READS.get()+((CURRENT.get(), 'memory_read', bounded({'kind': kind, 'contents': value, **versions}), versions),))


@safe
def flush_reads(db=None):
    pending = PENDING_READS.get()
    PENDING_READS.set(())
    if not pending:
        return
    with transaction(db) as conn:
        for trace, kind, value, versions in pending:
            token = CURRENT.set(trace)
            try:
                with local_span(conn, 'Retrieve document passages' if kind == 'retrieved_passages' else 'Read '+value['kind'], 'context' if kind == 'retrieved_passages' else 'memory'):
                    aid = artifact(kind, value, db=conn)
                    record('sources.retrieved' if kind == 'retrieved_passages' else 'memory.read',
                        'context' if kind == 'retrieved_passages' else 'memory', db=conn,
                        message='Retrieved document passages' if kind == 'retrieved_passages' else 'Retrieved '+value['kind'],
                        artifact_id=aid, **versions)
            finally:
                CURRENT.reset(token)


@safe
def memory_write(db, kind, before, after):
    # These rows COMMIT or ROLLBACK atomically with the memory transaction.
    flush_reads(db)
    with local_span(db, 'Commit '+kind):
        aid = artifact('memory_change', {'kind': kind, 'before': before, 'after': after}, 'produced', db=db)
        record('memory.committed', 'memory', db=db, message='Committed '+kind+' update', artifact_id=aid,
               revision=after.get('memory_revision', after.get('revision')))


@safe
def link_event(db, entry):
    trace = CURRENT.get()
    if trace:
        _event(db, trace, 'application.'+entry.type, 'tools' if entry.type == 'tool_call' else 'agents',
            'completed', {'event_id': entry.id, 'event_type': entry.type})
        if entry.type in ('intervention', 'assessment', 'plan', 'reflection', 'hypotheses', 'plan_suggested'):
            artifact(entry.type, entry.data, 'produced', db=db)
        if entry.type == 'intervention':
            meta = entry.data.get('metadata', {})
            record('sources.cited', 'context', db=db,
                source_ids=meta.get('source_ids', [meta['source_id']] if meta.get('source_id') else []),
                message='Only explicit output references are citations.')


@safe
def message_links(db, session_id):
    return {e.data['event_id']: e.run_id for e in db.scalars(select(Event).where(
        Event.session_id == session_id, Event.type.in_(['application.student_message', 'application.intervention'])))}


def iso(value):
    return value.isoformat()+'Z' if value and value.tzinfo is None else value.isoformat() if value else None


def run_view(row):
    return dict(id=row.id, name=row.name, status=row.status, session_id=row.session_id,
        operation_id=row.operation_id, material_id=row.material_id, trigger=row.data,
        started_at=iso(row.started_at), ended_at=iso(row.ended_at))


def event_view(row):
    return dict(cursor=row.cursor, id=row.id, run_id=row.run_id, span_id=row.span_id,
        user_id=row.user_id, session_id=row.session_id, operation_id=row.operation_id,
        type=row.type, category=row.category, status=row.status,
        timestamp=iso(row.created_at), data=row.data)


def eligible_runs(db, user_id, session_id=None, operation_id=None):
    query = select(Run).where(Run.user_id == user_id)
    if session_id is not None:
        session = db.get(Session, session_id)
        if session is None or session.user_id != user_id:
            return query.where(False)
        material = session.state.get('material_id')
        query = query.where(or_(Run.session_id == session_id,
            (Run.material_id == material) & Run.session_id.is_(None)) if material else Run.session_id == session_id)
    if operation_id is not None:
        query = query.where(Run.operation_id == operation_id)
    return query


def watermark(db):
    return db.scalar(select(func.max(Event.cursor))) or 0


def history(db, user_id, session_id=None, operation_id=None, before=None, limit=20):
    mark = watermark(db)
    query = eligible_runs(db, user_id, session_id, operation_id)
    active = db.scalars(query.where(Run.status.in_(ACTIVE)).order_by(Run.started_at, Run.id)).all()
    page = query.where(Run.status.not_in(ACTIVE))
    # Cursor uses the immutable first event, never wall-clock timestamps.
    first = select(func.min(Event.cursor)).where(Event.run_id == Run.id).correlate(Run).scalar_subquery()
    if before:
        page = page.where(first < before)
    rows = db.scalars(page.order_by(first.desc()).limit(limit+1)).all()
    more = len(rows) > limit
    rows = rows[:limit]
    ids = [r.id for r in rows+active]
    firsts = dict(db.execute(select(Event.run_id, func.min(Event.cursor)).where(Event.run_id.in_(ids)).group_by(Event.run_id)).all()) if ids else {}
    return dict(runs=[{**run_view(r), 'cursor': firsts[r.id]} for r in rows],
        active_runs=[{**run_view(r), 'cursor': firsts[r.id]} for r in active],
        next_cursor=min([firsts[r.id] for r in rows], default=None) if more else None,
        watermark=mark, tracking='complete' if HEALTHY else 'incomplete')


def replay(db, user_id, session_id=None, operation_id=None, after=0, limit=200):
    ids = eligible_runs(db, user_id, session_id, operation_id).with_only_columns(Run.id)
    rows = db.scalars(select(Event).where(Event.run_id.in_(ids), Event.cursor > after)
        .order_by(Event.cursor).limit(limit+1)).all()
    has_more = len(rows) > limit
    return dict(events=[event_view(e) for e in rows[:limit]], has_more=has_more,
        cursor=rows[min(limit, len(rows))-1].cursor if rows else after, watermark=watermark(db))


def detail(db, row):
    spans = db.scalars(select(Span).where(Span.run_id == row.id).order_by(Span.started_at, Span.id)).all()
    events = db.scalars(select(Event).where(Event.run_id == row.id).order_by(Event.cursor)).all()
    aids = {a for s in spans for a in s.consumed+s.produced}
    aids.update(e.data['artifact_id'] for e in events if e.data.get('artifact_id'))
    artifacts = db.scalars(select(Artifact).where(Artifact.id.in_(aids), Artifact.user_id == row.user_id)).all()
    return dict(**run_view(row), spans=[dict(id=s.id, parent_id=s.parent_id, name=s.name,
        category=s.category, status=s.status, dependencies=s.dependencies, consumed=s.consumed,
        produced=s.produced, started_at=iso(s.started_at), ended_at=iso(s.ended_at), data=s.data) for s in spans],
        edges=([dict(source=row.data['parent_run_id'], target=row.data.get('root_span_id', row.id), kind='trigger')] if row.data.get('parent_run_id') else []) + [dict(source=e.data.get('source_span_id') or e.data['source_run_id'], target=e.span_id or row.id, kind='artifact', artifact_id=e.data['artifact_id'], source_run_id=e.data['source_run_id'], label=e.data['kind']+' v'+str(e.data['version'])) for e in events if e.type == 'context.dependency'] + [dict(source=d, target=s.id, kind=kind) for s in spans
               for kind, ds in [('parent', [s.parent_id] if s.parent_id else []), ('dependency', s.dependencies)] for d in ds],
        events=[event_view(e) for e in events], artifacts=[dict(id=a.id, kind=a.kind) for a in artifacts])


@safe
def attach_material(material_id):
    trace = CURRENT.get()
    if trace:
        with transaction() as conn:
            rows = conn.scalars(select(Run).where(Run.operation_id == trace.operation_id, Run.user_id == trace.user_id)).all() if trace.operation_id else [conn.get(Run, trace.run_id)]
            for row in rows:
                row.material_id = material_id
                _event(conn, Trace(row.id, row.user_id, row.session_id, row.operation_id), 'material.attached', 'context', 'completed', {'material_id': material_id})


@safe
def progress(kind, data):
    warning = kind in ('research_partial', 'research_unavailable')
    record('progress.'+kind, 'errors' if warning else 'tools', 'warnings' if warning else 'completed', **data)
    if warning and CURRENT.get():
        with transaction() as conn:
            row = conn.get(Run, CURRENT.get().run_id)
            row.data = {**row.data, 'warnings': True}
    if kind == 'discarded':
        outcome('discarded', data.get('message', 'Snapshot changed'))


@safe
def interrupt_remaining():
    with transaction() as conn:
        for row in conn.scalars(select(Run).where(Run.status.in_(ACTIVE))):
            trace = Trace(row.id, row.user_id, row.session_id, row.operation_id)
            row.status, row.ended_at = 'interrupted', utcnow()
            for entry in conn.scalars(select(Span).where(Span.run_id == row.id, Span.status.in_(ACTIVE))):
                entry.status, entry.ended_at = 'interrupted', utcnow()
            _event(conn, trace, 'run.interrupted', 'agents', 'interrupted', {'reason': 'Server restarted'})


async def dispatch(broadcast):
    """Exactly one dispatcher, ordered by persisted cursor; transport is at-least-once."""
    global HEALTHY
    try:
        with database.SessionLocal() as conn:
            after = watermark(conn)
    except Exception:
        after = 0
        HEALTHY = False
    sessions, announced = [], False
    while True:
        try:
            with database.SessionLocal() as conn:
                sessions = conn.scalars(select(Session)).all()
                rows = conn.scalars(select(Event).where(Event.cursor > after).order_by(Event.cursor).limit(500)).all()
                deliveries = {}
                for row in rows:
                    run = conn.get(Run, row.run_id)
                    targets = [row.session_id] if row.session_id else [s.id for s in sessions
                        if run.material_id and s.user_id == row.user_id and s.state.get('material_id') == run.material_id]
                    for sid in targets:
                        deliveries.setdefault(sid, []).append(event_view(row))
            for sid, items in deliveries.items():
                await broadcast(sid, {'type': 'workflow_events', 'events': items, 'watermark': items[-1]['cursor']})
            if rows:
                after = rows[-1].cursor
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            HEALTHY = False
            if not announced:
                for session in sessions:
                    await broadcast(session.id, {'type': 'workflow_tracking', 'tracking': 'incomplete'})
                announced = True
            LOG.warning('Workflow dispatcher incomplete (%s)', type(exc).__name__)
        await asyncio.sleep(.15)
