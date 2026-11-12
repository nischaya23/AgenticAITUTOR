"""Persistent, account-scoped progress with resumable server-sent events."""
import asyncio
import json
from contextvars import ContextVar
from uuid import uuid4
from sqlalchemy import select
from openai import APIStatusError
from . import config, logging_setup as logs, workflow
from .db import Operation, OperationEvent, utcnow

EMITTER = ContextVar('operation_emitter', default=None)
TASKS = set()


def create(factory, user_id, kind, data=None):
    with factory.begin() as db:
        row = Operation(id=uuid4().hex, user_id=user_id, kind=kind, status='running', data=data or {})
        db.add(row)
        db.flush()
        return row.id


def emit(factory, operation_id, kind, **data):
    with factory.begin() as db:
        db.add(OperationEvent(operation_id=operation_id,
            data=dict(kind=kind, timestamp=utcnow().isoformat(), **data)))


async def progress(kind, **data):
    workflow.progress(kind, data)
    callback = EMITTER.get()
    if callback:
        await callback(kind, **data)


def finish(factory, operation_id, status='ready', **data):
    with factory.begin() as db:
        row = db.get(Operation, operation_id)
        row.status = status
        row.data = {**row.data, **({'error':None} if status in ('ready','needs_clarification') else {}), **data}
    emit(factory, operation_id, status, **data)


def snapshot(factory, operation_id, user_id):
    with factory() as db:
        row = db.get(Operation, operation_id)
        if row is None or row.user_id != user_id:
            return None
        events = db.scalars(select(OperationEvent).where(OperationEvent.operation_id == operation_id)
            .order_by(OperationEvent.id)).all()
        return dict(id=row.id, kind=row.kind, status=row.status, **{k:v for k,v in row.data.items() if k!='retry_data'},
            events=[dict(id=e.id, **e.data) for e in events])


def spawn(factory, operation_id, work):
    async def execute():
        async def callback(kind, **data):
            emit(factory, operation_id, kind, **data)
        token = EMITTER.set(callback)
        try:
            result = await work()
            finish(factory, operation_id, 'needs_clarification' if isinstance(result,dict) and result.get('type')=='choices' else 'ready', result=result)
        except asyncio.CancelledError:
            workflow.outcome('cancelled', 'Operation cancelled')
            finish(factory, operation_id, 'interrupted', error='Operation interrupted. Retry when ready.')
            raise
        except Exception as exc:
            workflow.outcome('failed', 'Operation failed')
            logs.event('operation.failed', operation_id=operation_id, error=type(exc).__name__,
                       http_status=exc.status_code if isinstance(exc, APIStatusError) else None)
            if isinstance(exc,APIStatusError):
                if exc.status_code == 429:
                    message = 'The model provider rate or quota limit was reached. Retry later.'
                elif exc.status_code >= 500:
                    message = f'{config.LLM_PROVIDER.title()} is temporarily unavailable (HTTP {exc.status_code}). Try again shortly.'
                else:
                    message = f'The model provider rejected this preparation step (HTTP {exc.status_code}). Please retry.'
            else:
                message = str(exc) if isinstance(exc, ValueError) else 'Operation failed. Please retry.'
            finish(factory, operation_id, 'failed', error=message[:300])
        finally:
            EMITTER.reset(token)
    parent = workflow.CURRENT.get()
    with factory() as db:
        operation = db.get(Operation, operation_id)
    trace = workflow.create_run(operation.user_id, 'Visualization' if operation.kind == 'video' else 'Topic/upload preparation',
        session_id=operation.data.get('session_id'), operation_id=operation_id,
        trigger={'parent_run_id': parent.run_id if parent else None, 'retry_of': operation.data.get('retry_of')})
    async def run():
        with workflow.run(operation.kind, trace=trace, independent=True), logs.scope(
                operation_id=operation_id, operation_kind=operation.kind):
            await execute()
    task = asyncio.create_task(run())
    TASKS.add(task)
    def finished(done):
        TASKS.discard(done)
        if trace:
            workflow.finish('cancelled' if done.cancelled() else 'interrupted', trace=trace)
    task.add_done_callback(finished)
    return task


async def events(factory, operation_id, user_id, after=0, workflow_after=None):
    if workflow_after is None:
        with factory() as db:
            workflow_after = workflow.watermark(db) if after else 0
    while True:
        data = snapshot(factory, operation_id, user_id)
        if data is None:
            return
        for item in data['events']:
            if item['id'] > after:
                after = item['id']
                yield f"id: {after}\ndata: {json.dumps(item)}\n\n"
        with factory() as db:
            replay = workflow.replay(db, user_id, operation_id=operation_id, after=workflow_after, limit=500)
        if replay['events']:
            workflow_after = replay['cursor']
            yield 'data: ' + json.dumps(dict(kind='workflow_events', **replay)) + '\n\n'
        if data['status'] != 'running' and not replay['has_more']:
            return
        yield ': keep-alive\n\n'
        await asyncio.sleep(1)
