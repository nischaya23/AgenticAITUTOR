"""Evidence-linked, uncertain hypotheses and coalesced draft analysis.

Draft workers never acquire the teaching/grading lock or write learning memory.
Submission cancels queued/running draft work before using the analysis lock.
"""
import asyncio
from copy import deepcopy
from typing import Literal
from pydantic import Field
from . import workflow, config, prompts, utils
from .db import Session
from .response import content_key, has_content, response_for
from .logging_setup import event as log_event


class Evidence(utils.Schema):
    reference: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=500)


class Hypothesis(utils.Schema):
    id: str = Field(min_length=1, max_length=80)
    concept: str = Field(min_length=1, max_length=200)
    possible_gap: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence] = Field(min_length=1, max_length=8)
    diagnostic_focus: str = Field(min_length=1, max_length=500)
    status: Literal['tentative', 'supported', 'weakened', 'resolved']


class Analysis(utils.Schema):
    hypotheses: list[Hypothesis] = Field(max_length=8)


def identity(state):
    return (state.get('current_problem', {}).get('id'), state.get('plan_version', 0),
            state.get('response_revision', 0))


def view(state):
    panel = deepcopy(state.get('hypothesis_state') or {'status': 'empty', 'items': [], 'version': 0})
    if panel.get('question_id') and (state.get('ended') or (panel.get('question_id'), panel.get('plan_version'),
            panel.get('response_revision')) != identity(state)):
        panel['status'] = 'stale'
    return panel


def eligible(state):
    panel = view(state)
    if panel['status'] in ('ready', 'empty') and panel.get('items'):
        return panel['items']
    return state.get('assessed_hypotheses', [])


@workflow.stage('Hypothesis · Analyze response', 'agents')
async def analyze(context, prior, origin, reference, assessment=None, reflection=None):
    references = {reference}
    references.update(f"event:{item['id']}" for item in context.get('history', []))
    references.update(e['reference'] for h in prior for e in h.get('evidence', []))
    result = await utils.call_llm(prompts.ANALYZE_HYPOTHESES, dict(context=context,
        prior_hypotheses=prior, origin=origin, evidence_references=sorted(references),
        assessment=assessment, reflection=reflection), schema=Analysis, operation='ANALYZE_HYPOTHESES')
    items = result.model_dump()['hypotheses']
    if len({item['id'] for item in items}) != len(items):
        raise ValueError('Hypothesis IDs must be unique.')
    for item in items:
        if any(e['reference'] not in references for e in item['evidence']):
            raise ValueError('Hypothesis evidence must reference supplied evidence.')
        if origin == 'draft':
            item['status'] = 'tentative'
    return items


def panel_for(state, items, origin, status='ready', **extra):
    qid, plan, revision = identity(state)
    return dict(status=status if items else ('empty' if status == 'ready' else status), items=items,
        origin=origin, question_id=qid, plan_version=plan, response_revision=revision,
        version=state.get('hypothesis_state', {}).get('version', 0)+1, **extra)


class DraftScheduler:
    def __init__(self, factory, broadcast, track):
        self.factory = factory
        self.broadcast = broadcast
        self.track = track
        self.tasks = {}
        self.last_started = {}
        self.last_key = {}
        self.sleep = asyncio.sleep

    def cancel(self, sid):
        task = self.tasks.pop(sid, None)
        if task:
            task.cancel()

    def schedule(self, sid):
        if sid in self.tasks and not self.tasks[sid].done():
            return
        with self.factory() as db:
            row = db.get(Session, sid)
            trace = workflow.create_run(row.user_id, 'Background review · Hypothesis', session_id=sid,
                trigger={'snapshot': identity(row.state), 'draft_changed_at': row.state.get('draft_changed_at'), 'event_id': row.state.get('last_draft_event_id')}) if row else None
        task = asyncio.create_task(self._worker(sid, trace))
        self.tasks[sid] = task
        self.track(sid, task)
        def finished(done):
            if trace:
                workflow.finish('cancelled' if done.cancelled() else 'discarded', trace=trace)
            if self.tasks.get(sid) is done:
                self.tasks.pop(sid, None)
        task.add_done_callback(finished)

    def read(self, sid):
        with self.factory() as db:
            row = db.get(Session, sid)
            return deepcopy(row.state) if row else None

    def active(self, state):
        return state and not state.get('ended') and not state.get('assessment_pending') \
            and state.get('current_problem', {}).get('id') and not state['current_problem'].get('solved')

    async def _worker(self, sid, queued_trace=None):
        from .orchestrator import context_for
        try:
            while True:
                state = self.read(sid)
                if not self.active(state):
                    return
                response = response_for(state)
                key = content_key(response)
                token = (state['current_problem']['id'], state.get('plan_version', 0), key)
                if not has_content(response) or self.last_key.get(sid) == token:
                    return
                delay = max(state.get('draft_changed_at', utils.now()) + config.HYPOTHESIS_PAUSE_SECONDS,
                    self.last_started.get(sid, -1e12) + config.HYPOTHESIS_INTERVAL_SECONDS) - utils.now()
                if delay > 0:
                    await self.sleep(delay)
                    continue  # Coalesce to the latest persisted snapshot after the pause.
                with workflow.run('Background review · Hypothesis', session_id=sid, trace=queued_trace, independent=True, trigger={'snapshot': identity(state), 'draft_changed_at': state.get('draft_changed_at')}):
                    queued_trace = None
                    snapshot = identity(state)
                    workflow.record('hypothesis.snapshot', snapshot=snapshot, trigger_event_id=state.get('last_draft_event_id'))
                    self.last_started[sid] = utils.now()
                    self.last_key[sid] = token
                    with self.factory.begin() as db:
                        current = db.get(Session, sid)
                        context = context_for(db, sid)
                        current.state = {**current.state, 'hypothesis_state': panel_for(state,
                            state.get('hypothesis_state', {}).get('items', []), 'draft', 'analyzing')}
                    await self.broadcast(sid, {'type': 'hypotheses', 'state': view(current.state)})
                    log_event('hypothesis.start', session_id=sid, origin='draft', snapshot=snapshot)
                    try:
                        items = await analyze(context, eligible(state), 'draft',
                            f'draft:{snapshot[0]}:{snapshot[2]}')
                        status, error = 'ready', None
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        log_event('hypothesis.failed', session_id=sid, origin='draft', error=type(exc).__name__)
                        workflow.record('hypothesis.fallback', 'errors', 'warnings', message='Draft analysis unavailable')
                        items, status, error = state.get('hypothesis_state', {}).get('items', []), 'unavailable', 'Draft analysis unavailable.'
                    with self.factory.begin() as db:
                        current = db.get(Session, sid)
                        fresh = deepcopy(current.state)
                        if identity(fresh) != snapshot or not self.active(fresh):
                            workflow.outcome('discarded', 'Response snapshot changed', db=db)
                            log_event('hypothesis.discarded', session_id=sid, snapshot=snapshot)
                            continue
                        panel = panel_for(fresh, items, 'draft', status, error=error)
                        current.state = {**fresh, 'hypothesis_state': panel}
                        workflow.version_artifact('hypotheses', panel['items'], panel['version'], 'produced', db=db)
                        workflow.artifact('hypothesis_snapshot', panel, 'produced', db=db)
                    await self.broadcast(sid, {'type': 'hypotheses', 'state': panel})
        except asyncio.CancelledError:
            log_event('hypothesis.discarded', session_id=sid, reason='submission, question change, or shutdown')
            raise
        except Exception as exc:
            log_event('hypothesis.failed', session_id=sid, error=type(exc).__name__)
