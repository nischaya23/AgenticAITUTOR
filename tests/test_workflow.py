"""Workflow evidence is tested at transaction, concurrency and transport boundaries."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import func, select, text
from backend import context, db, main, memory, operations, utils, workflow
from conftest import REAL_CALL_LLM


def session(client):
    return client.post('/api/sessions').json()['id']


def get_detail(client, rid):
    result = client.get('/api/workflow/runs/'+rid)
    assert result.status_code == 200, result.text
    return result.json()


def artifacts(client, detail, kind):
    return [client.get('/api/workflow/artifacts/'+a['id']).json()['data']
            for a in detail['artifacts'] if a['kind'] == kind]


async def test_overlapping_workers_keep_independent_status_and_evidence(app_env):
    client, factory, _ = app_env
    sid = session(client)
    first_started, release = asyncio.Event(), asyncio.Event()
    ids = {}

    async def first():
        with workflow.run('Observer', session_id=sid, independent=True) as trace:
            ids['observer'] = trace.run_id
            with workflow.span('Observer review'):
                workflow.artifact('input', {'response_revision': 12, 'agent': 'Observer'})
                first_started.set()
                await release.wait()
                workflow.record('observer.decision', decision='WAIT')

    task = asyncio.create_task(first())
    await first_started.wait()
    with workflow.run('Hypothesis', session_id=sid, independent=True) as trace:
        ids['hypothesis'] = trace.run_id
        with workflow.span('Draft analysis'):
            workflow.artifact('input', {'response_revision': 13, 'agent': 'Hypothesis'})
    page = client.get(f'/api/sessions/{sid}/workflow?limit=1').json()
    assert [r['id'] for r in page['active_runs']] == [ids['observer']]
    assert page['runs'][0]['id'] == ids['hypothesis']
    assert get_detail(client, ids['observer'])['status'] == 'running'
    assert artifacts(client, get_detail(client, ids['observer']), 'input') == [{'response_revision': 12, 'agent': 'Observer'}]
    assert artifacts(client, get_detail(client, ids['hypothesis']), 'input') == [{'response_revision': 13, 'agent': 'Hypothesis'}]
    release.set()
    await task
    assert get_detail(client, ids['observer'])['status'] == 'completed'
    assert workflow.CURRENT.get() is None


async def test_provider_boundary_captures_compacted_public_input_and_queue(app_env, monkeypatch):
    client, factory, _ = app_env
    sid = session(client)
    response = SimpleNamespace(output_text='A helpful response', usage=SimpleNamespace(input_tokens=25, output_tokens=8))
    create = AsyncMock(return_value=response)
    monkeypatch.setattr(utils, 'get_client', lambda: SimpleNamespace(responses=SimpleNamespace(create=create)))
    monkeypatch.setattr(utils, 'call_llm', REAL_CALL_LLM)
    loop = asyncio.get_running_loop()
    limiter = asyncio.Semaphore(0)
    utils._PROVIDER_LIMITS[loop] = limiter
    payload = {'snapshot': {'response_revision': 12, 'memory_revision': 8},
        'response': {'text': 'Student answer'}, 'workspace': 'Student answer',
        'problem': {'prompt': 'A question', 'expected_answer': 'PROTECTED ANSWER', 'rubric': 'PROTECTED RUBRIC'},
        'student': {'memory_revision': 8, 'policy': {'secret': 'PROTECTED POLICY'}},
        'plan': {'steps': ['Explore']}, 'plan_version': 2,
        'knowledge': [{'id':'D1','text':'included passage','url':'https://example.com/source'}]}
    ids = []
    async def work():
        with workflow.run('Tutor', session_id=sid, independent=True) as trace:
            ids.append(trace.run_id)
            workflow.retrieved(payload['knowledge']+[{'id':'D2','text':'retrieved only'}])
            result = await utils.call_llm('PRIVATE SYSTEM INSTRUCTIONS', payload, operation='TUTOR_RESPONSE')
            assert result == 'A helpful response'
            with factory.begin() as conn:
                db.append_event(conn, sid, 'intervention', {'type':'explanation','content':result,
                    'metadata':{'source_ids':['D1']}})
    task = asyncio.create_task(work())
    for _ in range(10):
        await asyncio.sleep(.01)
        if ids:
            break
    queued = get_detail(client, ids[0])
    assert any(s['name'] == 'Provider slot' and s['status'] == 'running' for s in queued['spans'])
    limiter.release()
    await task
    utils._PROVIDER_LIMITS.pop(loop, None)
    detail = get_detail(client, ids[0])
    captured = artifacts(client, detail, 'model_input')[0]['input']
    sent = json.loads(create.call_args.kwargs['input'])
    assert captured == workflow.public(sent)
    assert 'workspace' not in captured
    assert captured['snapshot']['response_revision'] == 12
    combined = json.dumps([client.get('/api/workflow/artifacts/'+a['id']).json() for a in detail['artifacts']])
    for protected in ('PROTECTED ANSWER','PROTECTED RUBRIC','PROTECTED POLICY','PRIVATE SYSTEM INSTRUCTIONS'):
        assert protected not in combined
    assert artifacts(client, detail, 'retrieved_passages')[0]['passages'][1]['id'] == 'D2'
    assert len(artifacts(client, detail, 'included_knowledge')[0]['value']) == 1
    assert next(e for e in detail['events'] if e['type'] == 'sources.cited')['data']['source_ids'] == ['D1']
    assert create.await_count == 1
    assert next(s for s in detail['spans'] if s['name'] == 'TUTOR_RESPONSE')['data']['input_tokens'] == 25
    assert next(s for s in detail['spans'] if s['name'] == 'Provider slot')['data']['queue_ms'] >= 0


def test_memory_updates_and_artifacts_commit_or_rollback_together(app_env):
    client, factory, _ = app_env
    sid = session(client)
    with workflow.run('Memory correction', session_id=sid) as trace:
        with pytest.raises(ValueError), factory.begin() as conn:
            memory.update_long_term_memory(conn, 1, {'memories':['ROLLBACK VALUE']})
            raise ValueError('rollback')
        before = get_detail(client, trace.run_id)
        assert not any(e['type'] == 'memory.committed' for e in before['events'])
        with factory.begin() as conn:
            memory.update_long_term_memory(conn, 1, {'memories':['Committed value']})
            # A second connection cannot see the update card before commit.
            assert not any(e['type'] == 'memory.committed' for e in get_detail(client, trace.run_id)['events'])
    detail = get_detail(client, trace.run_id)
    changes = artifacts(client, detail, 'memory_change')
    assert len(changes) == 1
    assert changes[0]['after']['memories'] == ['Committed value']
    assert 'ROLLBACK VALUE' not in json.dumps(detail)
    before_count = len(detail['events'])
    client.get(f'/api/sessions/{sid}/memory')
    client.get(f'/api/sessions/{sid}/workflow')
    assert len(get_detail(client, trace.run_id)['events']) == before_count
    # Snapshots are immutable when a later update occurs.
    with factory.begin() as conn:
        memory.update_long_term_memory(conn, 1, {'memories':['Later memory']})
    assert artifacts(client, get_detail(client, trace.run_id), 'memory_change') == changes


def test_context_dependencies_match_consumed_version_not_completion_order(app_env):
    client, _, _ = app_env
    sid = session(client)
    versions = {}
    for version in (3, 4):
        with workflow.run('Hypothesis '+str(version), session_id=sid, independent=True) as trace:
            with workflow.span('Generate hypotheses'):
                versions[version] = (trace.run_id, workflow.version_artifact('hypotheses', [{'id':str(version)}], version, 'produced'))
    with workflow.run('Observer', session_id=sid, independent=True) as consumer:
        with workflow.span('Review'):
            workflow.model_context({'snapshot': {'hypothesis_version':3}, 'hypotheses':[{'id':'3'}]})
    detail = get_detail(client, consumer.run_id)
    dependency = next(e for e in detail['events'] if e['type'] == 'context.dependency')
    assert dependency['data']['source_run_id'] == versions[3][0]
    assert dependency['data']['artifact_id'] == versions[3][1]
    assert versions[4][1] not in [a['id'] for a in detail['artifacts']]
    assert any(e['artifact_id'] == versions[3][1] for e in detail['edges'] if e['kind'] == 'artifact')


async def test_distinct_outcomes_recovery_and_restart(app_env):
    client, factory, _ = app_env
    sid = session(client)
    with workflow.run('Recovered render', session_id=sid) as recovered:
        try:
            with workflow.span('Attempt 1'):
                raise ValueError('invalid script')
        except ValueError:
            workflow.record('repair', 'tools', 'warnings', attempt=2)
        with workflow.span('Attempt 2'):
            workflow.record('render.complete')
    assert get_detail(client, recovered.run_id)['status'] == 'warnings'
    with workflow.run('Stale result', session_id=sid) as stale:
        workflow.outcome('discarded', 'Response changed')
    assert get_detail(client, stale.run_id)['status'] == 'discarded'
    async def cancelled():
        with workflow.run('Cancelled', session_id=sid) as trace:
            with workflow.span('Generating'):
                raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await cancelled()
    queued = workflow.create_run(1, 'Queued at restart', session_id=sid)
    workflow.interrupt_remaining()
    assert get_detail(client, queued.run_id)['status'] == 'interrupted'
    page = client.get(f'/api/sessions/{sid}/workflow').json()
    assert not page['active_runs']
    assert {'warnings','discarded','cancelled','interrupted'} <= {r['status'] for r in page['runs']}


def test_history_replay_pagination_ownership_and_preparation_links(app_env):
    client, factory, _ = app_env
    sid = session(client)
    oid = operations.create(factory, 1, 'topic')
    with workflow.run('Preparation', user_id=1, operation_id=oid) as preparation:
        workflow.attach_material('material-test')
    with factory.begin() as conn:
        conn.add(db.Material(id='material-test',user_id=1))
        memory.update_short_term_memory(conn,sid,{'material_id':'material-test'})
    queued = workflow.create_run(1, 'Pending worker', session_id=sid)
    for index in range(5):
        with workflow.run('Request '+str(index), session_id=sid):
            workflow.record('request')
    first = client.get(f'/api/sessions/{sid}/workflow?limit=2').json()
    assert [r['id'] for r in first['active_runs']] == [queued.run_id]
    seen, cursor = set(), None
    while True:
        page = client.get(f'/api/sessions/{sid}/workflow?limit=2'+(f'&before={cursor}' if cursor else '')).json()
        ids = {r['id'] for r in page['runs']}
        assert not seen & ids
        seen |= ids
        assert queued.run_id in {r['id'] for r in page['active_runs']}
        cursor = page['next_cursor']
        if not cursor:
            break
    assert preparation.run_id in seen
    operation_page = client.get('/api/operations/'+oid+'/workflow').json()
    assert operation_page['runs'][0]['id'] == preparation.run_id
    replayed, after = [], 0
    while True:
        replay = client.get(f'/api/sessions/{sid}/workflow/events?after={after}&limit=3').json()
        replayed += replay['events']; after = replay['cursor']
        if not replay['has_more']:
            break
    assert len({e['id'] for e in replayed}) == len(replayed)
    assert [e['cursor'] for e in replayed] == sorted(e['cursor'] for e in replayed)
    assert client.get(f'/api/sessions/{sid}/workflow/events?after={after}').json()['events'] == []
    with workflow.run('Artifact owner', session_id=sid) as owner:
        aid = workflow.artifact('input', {'public':'data'})
    client.post('/api/auth/logout')
    client.post('/api/auth/signup', json={'email':'other@local.test','password':'test-password'})
    for path in (f'/api/sessions/{sid}/workflow', '/api/workflow/runs/'+owner.run_id,
                 '/api/workflow/artifacts/'+aid, '/api/operations/'+oid+'/workflow'):
        assert client.get(path).status_code == 404


async def test_workflow_stream_is_committed_ordered_and_does_not_replace_legacy_messages(app_env):
    client, factory, _ = app_env
    sid = session(client)
    received = []
    async def broadcast(sid, data):
        received.extend(data['events'])
    dispatcher = asyncio.create_task(workflow.dispatch(broadcast))
    await asyncio.sleep(.01)
    with workflow.run('Live', session_id=sid) as trace:
        with factory.begin() as conn:
            workflow.record('not_yet_committed', db=conn)
            assert not any(e['type'] == 'not_yet_committed' for e in received)
        workflow.record('next_committed')
    await asyncio.sleep(.2)
    dispatcher.cancel()
    await asyncio.gather(dispatcher, return_exceptions=True)
    assert received
    assert [e['cursor'] for e in received] == sorted(e['cursor'] for e in received)
    assert len({e['id'] for e in received}) == len(received)
    replay = client.get(f'/api/sessions/{sid}/workflow/events').json()['events']
    assert {e['id'] for e in received} <= {e['id'] for e in replay}


def test_tracking_failure_does_not_stop_tutoring(app_env, monkeypatch):
    client, factory, _ = app_env
    def unavailable(*args, **kwargs):
        raise RuntimeError('tracking unavailable')
    monkeypatch.setattr(workflow, '_event', unavailable)
    sid = session(client)
    result = client.post(f'/api/sessions/{sid}/problem',json={'concept':'Binary Search'})
    assert result.status_code == 200
    assert result.json()['state']['current_problem']['prompt']
    assert client.get(f'/api/sessions/{sid}/workflow').json()['tracking'] == 'incomplete'


async def test_sdk_retry_attempts_are_visible_without_extra_model_invocations(app_env, monkeypatch):
    import httpx
    from openai import AsyncOpenAI
    client, _, _ = app_env
    sid = session(client)
    attempts = []
    def transport(request):
        attempts.append(request)
        if len(attempts) == 1:
            return httpx.Response(503, json={'error':{'message':'temporary'}},
                                  headers={'x-goog-request-id':'trace-123','retry-after':'0'})
        return httpx.Response(200, json={'id':'resp_test','object':'response','created_at':1,
            'model':'test-model','output':[{'id':'msg','type':'message','role':'assistant','status':'completed',
                'content':[{'type':'output_text','text':'Done','annotations':[]}]}],
            'usage':{'input_tokens':10,'output_tokens':2,'total_tokens':12}})
    async with AsyncOpenAI(api_key='credential-must-stay-private', max_retries=1,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport),
            event_hooks={'request':[utils.provider_request],'response':[utils.provider_response]})) as provider:
        monkeypatch.setattr(utils,'get_client',lambda:provider)
        before=utils.METRICS['llm_calls']
        with workflow.run('Tutor retry',session_id=sid) as trace:
            result=await REAL_CALL_LLM('Private instructions', {'query':'Help'}, operation='TUTOR_RESPONSE')
    detail=get_detail(client,trace.run_id)
    assert result == 'Done'
    assert utils.METRICS['llm_calls']-before == 1
    assert detail['status'] == 'warnings'
    assert [e['data']['attempt'] for e in detail['events'] if e['type']=='provider.attempt'] == [1,2]
    responses = [e['data'] for e in detail['events'] if e['type']=='provider.response']
    assert [item['http_status'] for item in responses] == [503,200]
    assert responses[0]['provider_request_id'] == 'trace-123'
    assert responses[0]['retry_after_seconds'] == 0
    assert responses[0]['duration_ms'] >= 0
    assert 'credential-must-stay-private' not in json.dumps(detail)


async def test_video_operation_stays_active_after_script_run_and_discards_stale_output(app_env):
    client, factory, _ = app_env
    sid=session(client)
    started,release=asyncio.Event(),asyncio.Event()
    async def work():
        with workflow.span('Render and deliver', 'tools'):
            started.set()
            await release.wait()
        await operations.progress('discarded', message='Question changed; animation not delivered.')
        return {'discarded':True}
    with workflow.run('Tutor script',session_id=sid) as script:
        oid=operations.create(factory,1,'video',{'session_id':sid})
        task=operations.spawn(factory,oid,work)
    await started.wait()
    page=client.get(f'/api/sessions/{sid}/workflow').json()
    renderer=next(r for r in page['active_runs'] if r['operation_id']==oid)
    assert renderer['id'] != script.run_id
    assert renderer['trigger']['parent_run_id'] == script.run_id
    assert get_detail(client,script.run_id)['status'] == 'completed'
    release.set()
    await task
    assert get_detail(client,renderer['id'])['status'] == 'discarded'
    assert operations.snapshot(factory,oid,1)['result']['discarded']


def test_preparation_clarification_runs_attach_once_to_each_studying_session(app_env):
    client,factory,_=app_env
    oid=operations.create(factory,1,'topic')
    with workflow.run('Interpret topic',user_id=1,operation_id=oid) as interpretation:
        workflow.record('clarification.requested', meanings=['Meaning A','Meaning B'])
    with workflow.run('Prepare chosen topic',user_id=1,operation_id=oid) as preparation:
        workflow.attach_material('shared-preparation')
    sids=[session(client),session(client)]
    with factory.begin() as conn:
        conn.add(db.Material(id='shared-preparation',user_id=1))
        for sid in sids:
            memory.update_short_term_memory(conn,sid,{'material_id':'shared-preparation'})
    for sid in sids:
        page=client.get(f'/api/sessions/{sid}/workflow').json()
        ids=[r['id'] for r in page['runs']]
        assert ids.count(interpretation.run_id)==ids.count(preparation.run_id)==1
    with factory() as conn:
        assert conn.scalar(select(func.count()).select_from(db.WorkflowRun).where(db.WorkflowRun.operation_id==oid))==2
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):
            conn.execute(text('DELETE FROM workflow_events'))
        conn.rollback()


def test_websocket_workflow_opt_in_keeps_legacy_protocol_compatible(app_env):
    client,_,_=app_env
    sid=session(client)
    assert client.post(f'/api/sessions/{sid}/problem',json={'concept':'Binary Search'}).status_code==200
    with client.websocket_connect(f'/ws/{sid}?workflow=1') as socket:
        assert socket.receive_json()['type']=='observer_status'
        socket.send_json({'type':'student_action','action':'idle'})
        batches=[]
        for _ in range(10):
            message=socket.receive_json()
            if message['type']=='workflow_events':
                batches.extend(message['events'])
                if any(e['type']=='span.completed' and e['data'].get('name')=='Record student activity' for e in batches):
                    break
        assert any(e['type']=='span.completed' and e['data'].get('name')=='Record student activity' for e in batches)
        assert [e['cursor'] for e in batches]==sorted(e['cursor'] for e in batches)
