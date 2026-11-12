import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from backend import assessment, config, hypothesis, main, memory, observer, orchestrator, reflection, tools, utils
from backend.db import Event, Session
from backend.response import Response, content_key
from test_study import prepare


def diagram():
    return dict(nodes=[dict(id='a', label='Start', position=dict(x=0, y=0)),
                       dict(id='b', label='End', position=dict(x=200, y=0))],
                edges=[dict(id='ab', source='a', target='b')])


def ingest(factory, sid, **event):
    with factory.begin() as db:
        return orchestrator.ingest(db, sid, utils.ClientEvent(**event))


def item(reference='event:1'):
    return dict(id='gap', concept='Binary Search', possible_gap='May confuse interval boundaries.',
        confidence=.4, evidence=[dict(reference=reference, summary='Boundary update needs explanation.')],
        diagnostic_focus='Trace an equal midpoint.', status='supported')


def test_text_only_response_rejects_diagrams():
    with pytest.raises(ValidationError): Response(text='answer',diagram=diagram())
    assert content_key({'text':'First'})!=content_key({'text':'Second'})


async def test_unified_response_is_atomic_persistent_and_submission_is_immutable(app_env, monkeypatch):
    client, factory, _ = app_env
    sid, _, study = prepare(client)
    submitted = dict(text='A complete explanation.')
    job = ingest(factory, sid, type='problem_attempt', response=submitted,
                 problem_id=study['state']['current_problem']['id'])
    ingest(factory, sid, type='student_action', action='edit', response=dict(text='Later edits'))
    grade = AsyncMock(return_value=dict(correct=False, score=.3, reason='Explain the edge.',
        verified=False, method='llm', misconceptions=[]))
    monkeypatch.setattr(assessment, 'evaluate_attempt', grade)
    await orchestrator.handle(job)
    assert grade.call_args.args[1] == submitted
    assert grade.call_args.args[2]['response'] == submitted
    saved = client.get(f'/api/sessions/{sid}').json()['state']
    assert saved['response']['text'] == 'Later edits'
    assert saved['current_problem']['id'] == study['state']['current_problem']['id']
    assert saved['hypothesis_state']['status'] == 'stale'
    with factory() as db:
        event = db.scalar(select(Event).where(Event.session_id == sid, Event.type == 'assessment'))
        assert event.data['response'] == submitted
        assert memory.get_long_term_memory(db, db.get(Session, sid).user_id)['hypotheses']


async def test_text_grade_uses_full_response(app_env, monkeypatch):
    client, factory, _ = app_env
    sid, _, study = prepare(client)
    response = Response(text='A complete explanation.').model_dump()
    job = ingest(factory, sid, type='problem_attempt', response=response,
                 problem_id=study['state']['current_problem']['id'])
    model = AsyncMock(return_value=assessment.Assessment(correct=True, score=1,
        reason='Valid directed construction.', misconceptions=[]))
    original = utils.call_llm
    async def route(*args, **kwargs):
        return await (model(*args, **kwargs) if kwargs.get('schema') is assessment.Assessment else original(*args, **kwargs))
    monkeypatch.setattr(utils, 'call_llm', route)
    monkeypatch.setattr(assessment, 'evaluate_attempt', REAL_GRADE)
    outputs = await orchestrator.handle(job)
    result = outputs[0]['result']
    assert result['correct'] and not result['verified']
    assert model.call_args.args[1]['answer'] == response
    with factory() as db:
        state = db.get(Session, sid).state
        assert state['current_problem']['solved']
        assert state['response'] == response


REAL_GRADE = assessment.evaluate_attempt


def test_legacy_workspace_adapter_and_response_persistence(app_env):
    client, factory, _ = app_env
    sid, _, _ = prepare(client)
    with factory.begin() as db:
        row = db.get(Session, sid)
        row.state = {k: v for k, v in row.state.items() if k not in ('response', 'response_revision')}
        row.state = {**row.state, 'workspace': 'Legacy answer'}
    assert client.get(f'/api/sessions/{sid}').json()['state']['response']['text'] == 'Legacy answer'
    moved = dict(text='New response')
    job = ingest(factory, sid, type='student_action', action='edit', response=moved)
    saved = client.get(f'/api/sessions/{sid}').json()['state']
    assert saved['response'] == moved and saved['workspace'] == 'New response'
    assert job['draft_changed'] and saved['response_revision'] == 1
    assert not ingest(factory, sid, type='student_action', action='edit', response=moved)['draft_changed']


def test_diagram_annotations_are_no_longer_accepted():
    with pytest.raises(ValidationError):
        tools.ReasoningReviewArgs(content='Review',concept='Flow',issues=[],diagram_annotations=[])


@pytest.mark.parametrize('format', ['explanation', 'trace'])
async def test_all_question_formats_and_private_answers(app_env, monkeypatch, format):
    client, factory, _ = app_env
    sid, _, study = prepare(client)
    question = tools.Problem(prompt='Explain or draw the flow.', concept='Binary Search',
        expected_answer='PRIVATE REFERENCE', rubric='PRIVATE RUBRIC', format=format)
    monkeypatch.setattr(utils, 'call_llm', AsyncMock(return_value=question))
    generated = await tools.generate_problem('Binary Search', hypotheses=[item()],
        eligible_concepts=['Binary Search'], recent_formats=['trace'], plan=study['state']['plan'])
    with factory.begin() as db:
        memory.update_short_term_memory(db, sid, {'current_problem': generated})
    public = client.get(f'/api/sessions/{sid}').json()['state']
    assert public['current_problem']['format'] == format
    assert 'PRIVATE' not in str(public)
    payload = utils.call_llm.call_args.args[1]
    assert payload['hypotheses'][0]['id'] == 'gap'
    assert payload['recent_formats'] == ['trace']
    with pytest.raises(ValidationError):
        tools.Problem(prompt='Interpret', concept='x', expected_answer='x', rubric='x', format='diagram_interpretation')


async def test_reflection_hypothesis_failure_and_next_question_retry_keep_assessment(app_env, monkeypatch):
    client, factory, _ = app_env
    sid, _, study = prepare(client)
    qid = study['state']['current_problem']['id']
    monkeypatch.setattr(reflection, 'reflect_on_answer', AsyncMock(side_effect=RuntimeError('reflection')))
    monkeypatch.setattr(hypothesis, 'analyze', AsyncMock(side_effect=RuntimeError('hypothesis')))
    original = tools.generate_problem
    monkeypatch.setattr(tools, 'generate_problem', AsyncMock(side_effect=RuntimeError('question')))
    await main.run_job(ingest(factory, sid, type='problem_attempt', content='1', problem_id=qid))
    state = client.get(f'/api/sessions/{sid}').json()['state']
    assert state['last_assessment']['correct'] and state['question_generation_error']
    assert state['assessment_stages'] == {'reflection': 'unavailable', 'hypotheses': 'unavailable'}
    assert state['current_problem']['id'] == qid
    monkeypatch.setattr(tools, 'generate_problem', original)
    resumed = client.post(f'/api/sessions/{sid}/next', json={'problem_id': qid})
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()['state']['current_problem']['id'] != qid
    with factory() as db:
        assert len(list(db.scalars(select(Event).where(Event.session_id == sid, Event.type == 'assessment')))) == 1


async def test_hypotheses_propagate_to_correct_progression_and_skip(app_env, monkeypatch):
    client, factory, _ = app_env
    sid, _, study = prepare(client)
    original = tools.generate_problem
    generate = AsyncMock(side_effect=original)
    monkeypatch.setattr(tools, 'generate_problem', generate)
    await main.run_job(ingest(factory, sid, type='problem_attempt', content='1', problem_id=study['state']['current_problem']['id']))
    assert generate.call_args.kwargs['hypotheses']
    assert generate.call_args.kwargs['plan'] == study['state']['plan']
    assert generate.call_args.kwargs['recent_formats']
    current = client.get(f'/api/sessions/{sid}').json()['state']
    assert client.post(f'/api/sessions/{sid}/skip', json={'problem_id': current['current_problem']['id']}).status_code == 200
    assert generate.call_args.kwargs['hypotheses']


class Clock:
    def __init__(self):
        self.time = 1000.
        self.waits = []

    async def sleep(self, delay):
        future = asyncio.get_running_loop().create_future()
        self.waits.append((self.time + delay, future))
        await future

    async def advance(self, seconds):
        self.time += seconds
        for when, future in self.waits:
            if when <= self.time and not future.done(): future.set_result(None)
        for _ in range(10): await asyncio.sleep(0)


async def test_draft_debounce_rate_limit_coalescing_and_no_durable_updates(app_env, monkeypatch):
    client, factory, _ = app_env
    sid, _, _ = prepare(client)
    clock = Clock()
    monkeypatch.setattr(utils, 'now', lambda: clock.time)
    publish = AsyncMock()
    scheduler = hypothesis.DraftScheduler(factory, publish, lambda *_: None)
    scheduler.sleep = clock.sleep
    analyze = AsyncMock(side_effect=lambda *args: [item(args[3])])
    monkeypatch.setattr(hypothesis, 'analyze', analyze)
    with factory() as db:
        before = memory.get_long_term_memory(db, db.get(Session, sid).user_id)
    ingest(factory, sid, type='student_action', action='edit', content='First')
    scheduler.schedule(sid)
    await clock.advance(4)
    ingest(factory, sid, type='student_action', action='edit', content='Latest')
    await clock.advance(4)
    assert not analyze.await_count
    await clock.advance(4)
    assert analyze.await_count == 1
    assert analyze.call_args.args[0]['response']['text'] == 'Latest'
    ingest(factory, sid, type='student_action', action='edit', content='Second')
    scheduler.schedule(sid)
    await clock.advance(8)
    assert analyze.await_count == 1
    ingest(factory, sid, type='student_action', action='edit', content='Coalesced')
    await clock.advance(22)
    assert analyze.await_count == 2
    assert analyze.call_args.args[0]['response']['text'] == 'Coalesced'
    scheduler.schedule(sid)
    await clock.advance(60)
    assert analyze.await_count == 2
    with factory() as db:
        after = memory.get_long_term_memory(db, db.get(Session, sid).user_id)
        assert before == after
    assert all(call.args[1]['type'] == 'hypotheses' for call in publish.call_args_list)


@pytest.mark.parametrize('change', ['response', 'plan', 'question', 'end'])
async def test_running_draft_results_are_discarded_after_changes(app_env, monkeypatch, change):
    client, factory, _ = app_env
    sid, _, _ = prepare(client)
    entered, release = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(config, 'HYPOTHESIS_PAUSE_SECONDS', 0)
    monkeypatch.setattr(config, 'HYPOTHESIS_INTERVAL_SECONDS', 999)
    async def slow(*args):
        entered.set(); await release.wait(); return [item(args[3])]
    monkeypatch.setattr(hypothesis, 'analyze', slow)
    publish = AsyncMock()
    scheduler = hypothesis.DraftScheduler(factory, publish, lambda *_: None)
    ingest(factory, sid, type='student_action', action='edit', content='Draft')
    scheduler.schedule(sid)
    task = scheduler.tasks[sid]
    await asyncio.wait_for(entered.wait(), 2)
    with factory.begin() as db:
        row = db.get(Session, sid)
        state = deepcopy(row.state)
        if change == 'response': state['response_revision'] += 1
        if change == 'plan': state['plan_version'] += 1
        if change == 'question': state['current_problem']['id'] = 'new-question'
        if change == 'end': state['ended'] = True
        row.state = state
    release.set()
    for _ in range(10): await asyncio.sleep(0)
    assert publish.await_count == 1  # analyzing only; no stale result delivery
    scheduler.cancel(sid)
    await asyncio.gather(task, return_exceptions=True)


async def test_submission_cancels_draft_and_sessions_are_isolated(app_env, monkeypatch):
    client, factory, _ = app_env
    sid, _, study = prepare(client)
    other, _, _ = prepare(client)
    entered, cancelled = asyncio.Event(), asyncio.Event()
    monkeypatch.setattr(config, 'HYPOTHESIS_PAUSE_SECONDS', 0)
    original = hypothesis.analyze
    async def slow(*args, **kwargs):
        if args[2] != 'draft': return await original(*args, **kwargs)
        entered.set()
        try: await asyncio.Event().wait()
        except asyncio.CancelledError: cancelled.set(); raise
    monkeypatch.setattr(hypothesis, 'analyze', slow)
    scheduler = hypothesis.DraftScheduler(factory, AsyncMock(), lambda *_: None)
    ingest(factory, sid, type='student_action', action='edit', content='Draft')
    scheduler.schedule(sid)
    task = scheduler.tasks[sid]
    await asyncio.wait_for(entered.wait(), 2)
    scheduler.cancel(sid)
    await asyncio.wait_for(cancelled.wait(), 2)
    await asyncio.gather(task, return_exceptions=True)
    outputs = await orchestrator.handle(ingest(factory, sid, type='problem_attempt', content='wrong',
        problem_id=study['state']['current_problem']['id']))
    assert any(o['type'] == 'hypotheses' and o['state']['origin'] == 'submission' for o in outputs)
    assert client.get(f'/api/sessions/{other}').json()['state']['hypothesis_state']['status'] == 'empty'


def test_observer_activity_tracks_provisional_assessments_without_making_a_decision():
    state = dict(observer=dict(problem_started_at=0, last_action_at=0), current_problem={'id': 'q'})
    state['observer'] = observer.observe(state, 'assessment', {'correct': False, 'method': 'llm'}, 1)
    assert state['observer']['failure_streak'] == 1
    assert 'status' not in state['observer']
    assert observer.activity_context(state, 60)['seconds_since_activity'] == 60
    state['observer'] = observer.observe(state, 'assessment', {'correct': True, 'method': 'llm'}, 61)
    assert state['observer']['failure_streak'] == 0


async def test_hypothesis_evidence_validation_and_draft_status(monkeypatch):
    mock = AsyncMock(return_value=hypothesis.Analysis(hypotheses=[item('draft:q:1')]))
    monkeypatch.setattr(utils, 'call_llm', mock)
    items = await hypothesis.analyze({}, [], 'draft', 'draft:q:1')
    assert items[0]['status'] == 'tentative'
    with pytest.raises(ValueError): await hypothesis.analyze({}, [], 'draft', 'draft:q:2')


async def test_shared_provider_limit_caps_draft_and_foreground_requests(monkeypatch):
    from conftest import REAL_CALL_LLM
    monkeypatch.setattr(config, 'PROVIDER_CONCURRENCY', 2)
    utils._PROVIDER_LIMITS.pop(asyncio.get_running_loop(), None)
    running, peak = 0, 0
    async def call(*args, **kwargs):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0)
        running -= 1
        return 'done'
    monkeypatch.setattr(utils, '_call_llm', call)
    results = await asyncio.gather(*(REAL_CALL_LLM('instructions', {'origin': origin})
        for origin in ['draft', 'assessment', 'hypothesis', 'reflection', 'tool']))
    assert peak == 2 and results == ['done'] * 5
    utils._PROVIDER_LIMITS.pop(asyncio.get_running_loop(), None)


async def test_finishing_job_does_not_clear_a_new_submission_pending_flag(app_env, monkeypatch):
    client, factory, _ = app_env
    sid, _, study = prepare(client)
    qid = study['state']['current_problem']['id']
    second = None
    async def broadcast(_sid, message):
        nonlocal second
        if message['type'] == 'assessment' and second is None:
            second = ingest(factory, sid, type='problem_attempt', content='retry', problem_id=qid)
    monkeypatch.setattr(main, 'broadcast', broadcast)
    await main.run_job(ingest(factory, sid, type='problem_attempt', content='wrong', problem_id=qid))
    with factory() as db:
        state = db.get(Session, sid).state
        assert state['assessment_pending']
        assert state['pending_assessment_event_id'] == second['event_id']
    await orchestrator.handle(second)
