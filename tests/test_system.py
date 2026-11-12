import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession
from backend import config, db, features, knowledge, main, memory, orchestrator, planner, reflection, tools, tutor, utils
from conftest import receive_until


def create(client):
    response = client.post('/api/sessions')
    assert response.status_code == 200
    sid=response.json()['id']
    assert client.post(f'/api/sessions/{sid}/problem',json={'concept':'Binary Search'}).status_code==200
    with main.SessionLocal.begin() as db:
        state=memory.get_short_term_memory(db,sid)
        state['current_problem']['id']='binary-search-first'
        memory.update_short_term_memory(db,sid,state)
    return sid


def ingest(factory, sid, **data):
    with factory.begin() as connection:
        return orchestrator.ingest(connection, sid, utils.ClientEvent(**data))


def approved_help(factory, sid):
    return {**ingest(factory, sid, type='student_action', action='idle'),
            'kind': 'observer_review', 'observer_approved': True}


def test_tables_fk_append_only_and_restart(app_env):
    client, factory, engine = app_env
    sid = create(client)
    assert set(inspect(engine).get_table_names()) == {'users','sessions','events','student_state','concepts','concept_edges','logins','materials','memory_items','operations','operation_events','workflow_runs','workflow_spans','workflow_events','workflow_artifacts'}
    with factory.begin() as connection:
        row = connection.get(db.Session, sid)
        memory.update_long_term_memory(connection, row.user_id, {'memories':['I prefer invariants.']})
        memory.update_short_term_memory(connection, sid, {'workspace':'persistent work'})
    restarted = create_engine(engine.url)
    with DBSession(restarted) as connection:
        assert connection.get(db.Session, sid).state['workspace'] == 'persistent work'
        assert connection.get(db.StudentState, row.user_id).data['memories'] == ['I prefer invariants.']
    restarted.dispose()
    for statement in ('DELETE FROM events', "UPDATE events SET type='changed'"):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(text(statement))
    with pytest.raises(IntegrityError), factory.begin() as connection:
        db.append_event(connection, 999, 'student_message', {'content':'invalid FK'})


@pytest.mark.parametrize('kind', list(tools.REGISTRY))
async def test_all_five_tools_have_valid_outputs(app_env, kind):
    client, factory, _ = app_env
    sid = create(client)
    with factory() as connection:
        context = orchestrator.context_for(connection, sid)
    if kind == 'highlight':
        context['workspace'] = 'Check the interval boundaries.'
    result = await tutor.execute_intervention({'action':kind,'strategy':'one step'}, context)
    assert result['type'] == kind
    assert result['content']
    if kind == 'highlight':
        annotation = result['metadata']['annotations'][0]
        assert annotation['end'] > annotation['start']
        tools.validate_target(result, context)
    if kind == 'visualization':
        assert 'draw_frame' in result['metadata']['video_script']['code']
        assert result['metadata']['video_script']['chapters']
    definition = tools.function_definition(kind)
    assert definition['strict'] and definition['parameters']['additionalProperties'] is False


def test_explicit_websocket_ignores_proactive_preference(app_env):
    client, _, _ = app_env
    sid = create(client)
    with client.websocket_connect(f'/ws/{sid}') as socket:
        socket.send_json({'type':'preferences','proactive_enabled':False})
        receive_until(socket, 'ack')
        socket.send_json({'type':'student_message','content':'Explain lower bound','tool':'explanation'})
        receive_until(socket,'ack')
        output = receive_until(socket,'intervention')
        assert output['result']['type'] == 'explanation' and not output['proactive']
    restored = client.get(f'/api/sessions/{sid}').json()
    assert restored['state']['plan']['steps']
    assert any(e['type']=='intervention' for e in restored['history'])
    assert client.get('/api/student').json()['learning_goals']


def test_disabled_observer_never_starts_agents(app_env, monkeypatch):
    client, _, _ = app_env
    handle = AsyncMock()
    monkeypatch.setattr(orchestrator, 'handle', handle)
    sid = create(client)
    monkeypatch.setattr(utils, 'call_llm', AsyncMock(side_effect=AssertionError('Observer called an LLM')))
    with client.websocket_connect(f'/ws/{sid}') as socket:
        socket.send_json({'type':'student_action','action':'idle'})
        assert receive_until(socket,'ack')['action'] == 'idle'
    assert handle.await_count == 0


async def test_approved_help_reflection_and_mastery_persist(app_env):
    client, factory, _ = app_env
    sid = create(client)
    job = approved_help(factory, sid)
    outputs = await orchestrator.handle(job)
    assert outputs[0]['proactive']
    with factory() as connection:
        row = connection.get(db.Session, sid)
        assert row.state['observer']['pending_intervention']['event_id'] == outputs[0]['event_id']
    attempt = ingest(factory, sid, type='problem_attempt',content='1',problem_id='binary-search-first')
    outputs = await orchestrator.handle(attempt)
    assert outputs[0]['type'] == 'assessment' and outputs[0]['result']['correct']
    with factory() as connection:
        model = memory.get_long_term_memory(connection,row.user_id)
        assert model['effective_interventions']['guiding_question'] == {'observations': 1, 'reward_sum': .5}
        assert model['mastery']['Binary Search'] == .6
        assert model['memories']
        reflections = connection.scalars(select(db.Event).where(db.Event.type=='reflection')).all()
        assert next(r for r in reflections if 'reward' in r.data).data['reward'] == .5
        assert next(r for r in reflections if 'reward' in r.data).data['causal_claim'] is False


async def test_stale_proactive_response_is_discarded(app_env, monkeypatch):
    client, factory, _ = app_env
    sid = create(client)
    job = approved_help(factory, sid)
    entered, release = asyncio.Event(), asyncio.Event()
    original = tutor.respond_to_student
    async def slow(context, selection):
        entered.set()
        await release.wait()
        return await original(context, selection)
    monkeypatch.setattr(tutor,'respond_to_student',slow)
    task = asyncio.create_task(orchestrator.deliver_intervention(job,False))
    await asyncio.wait_for(entered.wait(),2)
    ingest(factory,sid,type='student_action',action='edit',content='I have changed my reasoning.')
    release.set()
    assert await asyncio.wait_for(task,2) is None
    with factory() as connection:
        assert not connection.scalar(select(db.Event.id).where(db.Event.type=='intervention'))
        assert not connection.get(db.Session,sid).state['observer'].get('pending_wake')


async def test_duplicate_trigger_does_not_deliver_twice(app_env):
    client, factory, _ = app_env
    sid = create(client)
    job=ingest(factory,sid,type='student_message',content='hint please',tool='hint')
    assert await orchestrator.deliver_intervention(job,True)
    assert await orchestrator.deliver_intervention(job,True) is None


async def test_planner_scores_delivered_tools_and_switches_after_a_hint(app_env, monkeypatch):
    client, factory, _ = app_env
    sid = create(client)
    problem_id = 'binary-search-first'
    tool_choice = 'hint'
    original_model = utils.call_llm
    model_scores = []

    async def model(instructions, data, **kwargs):
        if kwargs.get('schema') is planner.Selection:
            model_scores.append(data['eligible_tool_scores'])
            return planner.Selection(action=tool_choice, strategy='One step')
        return await original_model(instructions, data, **kwargs)

    monkeypatch.setattr(utils, 'call_llm', model)
    original_tutor = tutor.respond_to_student
    failure = AsyncMock(side_effect=ValueError('Tool failed before delivery.'))
    monkeypatch.setattr(tutor, 'respond_to_student', failure)
    with pytest.raises(ValueError, match='before delivery'):
        await orchestrator.handle(ingest(factory, sid, type='student_message', content='Help me get started.'))
    monkeypatch.setattr(tutor, 'respond_to_student', original_tutor)

    first = await orchestrator.handle(ingest(factory, sid, type='student_message', content='Help me get started.'))
    assert first[0]['result']['type'] == 'hint'
    assert model_scores[-1]['hint'] == 1
    assert 'highlight' not in model_scores[-1]
    saved = client.get(f'/api/sessions/{sid}').json()
    assert any(event['type'] == 'intervention' and event['data']['type'] == 'hint' for event in saved['history'])

    second = await orchestrator.handle(ingest(factory, sid, type='student_message', content='I still need help.'))
    assert second[0]['result']['type'] == 'explanation'
    assert 'hint' not in model_scores[-1]
    explicit = await orchestrator.handle(ingest(factory, sid, type='student_message', content='Give me another hint.', tool='hint'))
    assert explicit[0]['result']['type'] == 'hint'
    assert len(model_scores) == 3

    tool_choice = 'visualization'
    queued = AsyncMock(return_value={'type': 'video_pending', 'operation_id': 'v1'})
    monkeypatch.setattr(features, 'queue_video', queued)
    output = await orchestrator.handle(approved_help(factory, sid))
    assert output[0]['type'] == 'video_pending'
    assert model_scores[-1]['explanation'] == .5
    assert model_scores[-1]['visualization'] == 1
    assert queued.call_args.args[1]['type'] == 'visualization'
    with factory() as connection:
        assert planner.tool_scores(connection, sid, problem_id, '')['visualization'] == 1

    response = client.post(f'/api/sessions/{sid}/problem', json={'concept': 'Binary Search'})
    assert response.status_code == 200
    new_id = response.json()['state']['current_problem']['id']
    with factory.begin() as connection:
        assert planner.tool_scores(connection, sid, new_id, '')['hint'] == 1
        db.append_event(connection, sid, 'intervention', {'type': 'hint', 'content': 'Legacy hint'})
        assert 'hint' not in planner.tool_scores(connection, sid, new_id, '')


def test_legacy_policy_is_hidden_and_removed_on_memory_write(app_env):
    client,factory,_=app_env
    sid=create(client)
    with factory.begin() as connection:
        uid=connection.get(db.Session,sid).user_id
        student=connection.get(db.StudentState,uid)
        student.data={**student.data,'policy':{'updates':20}}
    assert 'policy' not in client.get('/api/student').json()
    with factory.begin() as connection:
        memory.update_long_term_memory(connection,uid,{'learning_goals':['Practice']})
    with factory() as connection:
        assert 'policy' not in connection.get(db.StudentState,uid).data


async def test_unverified_assessment_does_not_credit_delivered_help(app_env):
    client,factory,_=app_env
    sid=create(client)
    await orchestrator.handle(approved_help(factory,sid))
    with factory.begin() as connection:
        row=connection.get(db.Session,sid);state=memory.get_short_term_memory(connection,sid)
        assert reflection.reflect_on_intervention(connection,sid,row.user_id,state,{'verified':False,'correct':True},1) is None
        assert memory.get_long_term_memory(connection,row.user_id)['effective_interventions']=={}


async def test_feedback_is_linked_once_and_negative_outcome_replans(app_env):
    client,factory,_=app_env
    sid=create(client)
    output=(await orchestrator.handle(approved_help(factory,sid)))[0]
    with pytest.raises(ValueError):
        ingest(factory,sid,type='feedback',rating=1,intervention_id=999)
    job=ingest(factory,sid,type='feedback',rating=-1,intervention_id=output['event_id'])
    assert not job['replan']
    assert any(output['type'] == 'plan' for output in await orchestrator.handle(job))
    with factory() as connection:
        assert memory.get_long_term_memory(connection, connection.get(db.Session, sid).user_id)['effective_interventions']['guiding_question']['reward_sum'] == -1
    with pytest.raises(ValueError):
        ingest(factory,sid,type='feedback',rating=1,intervention_id=output['event_id'])


def test_material_ingestion_retrieval_graph_and_cycle_prevention(app_env):
    client,factory,_=app_env
    response=client.post('/api/materials',files={'file':('queues.md',b'# Queues\nA queue uses FIFO: first in first out.','text/markdown')})
    assert response.status_code==200
    assert knowledge.retrieve_knowledge('FIFO queue')[0]['source']=='queues.md'
    assert any(c['name']=='Queues' for c in client.get('/api/concepts').json()['nodes'])
    with factory.begin() as connection:
        graph=knowledge.ConceptGraph(concepts=['A','B','C'],prerequisites=[knowledge.Prerequisite(prerequisite=a,concept=b) for a,b in [('A','B'),('B','C'),('C','A')]])
        knowledge.store_graph(connection,graph)
        assert knowledge.get_prerequisites(connection,'A')==[]
        assert knowledge.get_prerequisites(connection,'C')==['B']
    assert client.post('/api/materials',files={'file':('bad.exe',b'bad','text/plain')}).status_code==400
    assert client.post('/api/materials',files={'file':('empty.txt',b'','text/plain')}).status_code==400


def test_validation_and_no_student_code_execution(app_env):
    client,factory,_=app_env
    sid=create(client)
    with client.websocket_connect(f'/ws/{sid}') as socket:
        assert socket.receive_json()['type']=='observer_status'
        socket.send_json({'type':'problem_attempt','content':'1','problem_id':'old'})
        assert socket.receive_json()['type']=='error'
        socket.send_json({'type':'code_execution','content':'__import__("os").system("touch /tmp/should-not-exist")','correct':True})
        assert socket.receive_json()['type']=='error'
        socket.send_json({'type':'code_execution','content':'This is a client-reported test run, not verified.'})
        receive_until(socket,'ack')
    with factory() as connection:
        assert not connection.get(db.Session,sid).state['observer'].get('failure_streak')
    assert 'expected_answer' not in client.get(f'/api/sessions/{sid}').json()['state']['current_problem']
    assert client.get('/api/sessions/9999').status_code==404


async def test_invalid_tool_metadata_rejected():
    with pytest.raises(ValueError):
        await tools.invoke_registered_tool('visualization',{'content':'bad','concept':'x','nodes':[{'id':'1','label':'x'}],'edges':[{'source':'1','target':'missing'}],'steps':[]})
    with pytest.raises(ValueError):
        tools.validate_target(tools.highlight({'content':'x','concept':'x','target':'workspace','source_id':'workspace','quote':'bad','start':0,'end':3}),{'workspace':'abc','knowledge':[]})


def test_new_problem_and_session_reflection(app_env):
    client,_,_=app_env
    sid=create(client)
    response=client.post(f'/api/sessions/{sid}/problem',json={'concept':'Binary Search'})
    assert response.status_code==200
    assert 'expected_answer' not in response.json()['state']['current_problem']
    with client.websocket_connect(f'/ws/{sid}') as socket:
        socket.send_json({'type':'session_end'})
        result=receive_until(socket,'session_summary')
        assert result['result']['next_goal']
    assert client.get(f'/api/sessions/{sid}').json()['state']['ended']
    assert not client.get('/api/student').json()['memories']


async def test_real_sdk_responses_protocol_with_mock_transport(app_env,monkeypatch):
    """Exercise real call_llm plumbing and SDK-shaped parse/function outputs, no paid calls."""
    client,factory,_=app_env
    sid=create(client)
    monkeypatch.setattr(config,'LLM_PROVIDER','openai')
    usage=SimpleNamespace(input_tokens=80,output_tokens=20)
    async def parse(**kwargs):
        schema=kwargs['text_format']
        assert kwargs['store'] is False and kwargs['model']==config.OPENAI_MODEL
        if schema is planner.Plan:
            parsed=planner.Plan(objective='Find lower bound',steps=['Track interval'],learning_goals=['Binary search'])
        else:
            parsed=planner.Selection(action='hint',strategy='One clue')
        return SimpleNamespace(output_parsed=parsed,usage=usage)
    async def create_response(**kwargs):
        assert kwargs['tools'][0]['type']=='function'
        if kwargs['tools'][0]['name']=='web_search':
            return SimpleNamespace(output=[SimpleNamespace(type='function_call',name='finish_research',call_id='research-finished',arguments=json.dumps({'reason':'No additional evidence needed.'}))],usage=usage)
        assert kwargs['tool_choice']=={'type':'function','name':'hint'}
        return SimpleNamespace(output=[SimpleNamespace(type='function_call',name='hint',arguments=json.dumps({'content':'Consider the left boundary.','concept':'Binary Search'}))],usage=usage)
    fake=SimpleNamespace(responses=SimpleNamespace(parse=AsyncMock(side_effect=parse),create=AsyncMock(side_effect=create_response)))
    monkeypatch.setattr(utils,'get_client',lambda:fake)
    from conftest import REAL_CALL_LLM as call_llm
    monkeypatch.setattr(utils,'call_llm',call_llm)
    output=await orchestrator.handle(ingest(factory,sid,type='student_message',content='Can I have a clue?'))
    assert output[0]['result']['content']=='Consider the left boundary.'
    assert utils.METRICS['llm_calls']==4 and utils.METRICS['output_tokens']==80
    assert fake.responses.parse.await_count==2 and fake.responses.create.await_count==2


async def test_repeated_correct_answer_cannot_inflate_mastery(app_env):
    client,factory,_=app_env
    sid=create(client)
    await orchestrator.handle(ingest(factory,sid,type='problem_attempt',content='1',problem_id='binary-search-first'))
    for _ in range(2):
        with pytest.raises(ValueError, match='already being assessed or solved'):
            ingest(factory,sid,type='problem_attempt',content='1',problem_id='binary-search-first')
    assert client.get('/api/student').json()['mastery']['Binary Search']==.6


async def test_reasoning_assessment_is_not_exact_answer_check(app_env):
    client,factory,_=app_env
    sid=create(client)
    output=await orchestrator.handle(ingest(factory,sid,type='problem_attempt',content='1',problem_id='binary-search-first',assessment_mode='reasoning'))
    assert output[0]['result']['verified'] is False


async def test_pre_intervention_attempt_is_not_credited_as_an_outcome(app_env):
    client,factory,_=app_env
    sid=create(client)
    job=approved_help(factory,sid)
    await orchestrator.handle(job)
    with factory.begin() as connection:
        row=connection.get(db.Session,sid);state=memory.get_short_term_memory(connection,sid)
        result=reflection.reflect_on_intervention(connection,sid,row.user_id,state,
            {'verified':True,'correct':True,'observed_at':job['received_at']-10},job['event_id'])
        assert result['reward'] is None
        assert memory.get_long_term_memory(connection,row.user_id)['effective_interventions']=={}


def test_multiple_materials_build_one_graph(app_env):
    client,_,_=app_env
    files=[('files',('queues.md',b'# Queues\nA queue uses FIFO ordering.','text/markdown')),
           ('files',('stacks.md',b'# Stacks\nA stack uses LIFO ordering.','text/markdown'))]
    response=client.post('/api/materials',files=files)
    assert response.status_code==200
    body=response.json()
    assert body['sources']==['queues.md','stacks.md']
    assert body['name']=='queues.md + stacks.md'
    assert {'Queues','Stacks'}<=set(body['concepts']['concepts'])
    material=knowledge.get_material(body['id'])
    assert {c['source'] for c in material['chunks']}=={'queues.md','stacks.md'}
    assert len({c['id'] for c in material['chunks']})==len(material['chunks'])
    assert knowledge.retrieve_knowledge('LIFO stack',material_id=body['id'])[0]['source']=='stacks.md'
    bad=client.post('/api/materials',files=[files[0],('files',('bad.exe',b'bad','text/plain'))])
    assert bad.status_code==400 and 'bad.exe' in bad.json()['detail']
    too_many=[('files',(f'm{i}.md',b'# M\ntext','text/markdown')) for i in range(11)]
    assert client.post('/api/materials',files=too_many).status_code==400


def test_legacy_mastery_records_do_not_block_new_sessions(app_env):
    client,factory,_=app_env
    from backend.db import StudentState
    assert client.post('/api/sessions').status_code==200
    with factory.begin() as db:
        row=db.scalars(__import__('sqlalchemy').select(StudentState)).first()
        row.data={**row.data,'mastery':{'Groups':.4,'Calculus':{'a':1.0,'b':3.0,'t':1.0},'Broken':{'x':1}}}
    assert client.post('/api/sessions').status_code==200
    with factory() as db:
        row=db.scalars(__import__('sqlalchemy').select(StudentState)).first()
        assert row.data['mastery']=={'Groups':.4,'Calculus':.25}
