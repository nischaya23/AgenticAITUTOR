import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import numpy as np
import pytest
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession
from backend import config, db, knowledge, main, memory, observer, orchestrator, planner, reflection, tools, tutor, utils
from conftest import receive_until


def create(client):
    response = client.post('/api/sessions')
    assert response.status_code == 200
    return response.json()['id']


def ingest(factory, sid, **data):
    with factory.begin() as connection:
        return orchestrator.ingest(connection, sid, utils.ClientEvent(**data))


def stalled(factory, sid):
    with factory.begin() as connection:
        state = memory.get_short_term_memory(connection, sid)
        state['observer'].update(failure_streak=3, last_progress_at=utils.now()-100,
                                 last_action_at=utils.now()-10, problem_started_at=utils.now()-150)
        memory.update_short_term_memory(connection, sid, state)


def test_six_tables_fk_append_only_and_restart(app_env):
    client, factory, engine = app_env
    sid = create(client)
    assert set(inspect(engine).get_table_names()) == {'users','sessions','events','student_state','concepts','concept_edges'}
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
        assert result['metadata']['end'] > result['metadata']['start']
        tools.validate_target(result, context)
    if kind == 'visualization':
        assert result['metadata']['nodes'] and result['metadata']['steps']
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


def test_wait_never_starts_agents(app_env, monkeypatch):
    client, _, _ = app_env
    handle = AsyncMock()
    monkeypatch.setattr(orchestrator, 'handle', handle)
    monkeypatch.setattr(utils, 'call_llm', AsyncMock(side_effect=AssertionError('Observer called an LLM')))
    sid = create(client)
    with client.websocket_connect(f'/ws/{sid}') as socket:
        socket.send_json({'type':'student_action','action':'idle'})
        assert receive_until(socket,'ack')['decision'] == 'WAIT'
    assert handle.await_count == 0


async def test_wake_reflection_bandit_and_mastery_persist(app_env):
    client, factory, _ = app_env
    sid = create(client)
    stalled(factory, sid)
    job = ingest(factory, sid, type='student_action', action='idle')
    assert job['decision'] == 'WAKE'
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
        assert model['policy']['updates'] == 1
        assert model['mastery']['Binary Search'] == .6
        assert model['memories']
        reflections = connection.scalars(select(db.Event).where(db.Event.type=='reflection')).all()
        assert reflections[0].data['reward'] == .5
        assert reflections[0].data['causal_claim'] is False


async def test_stale_proactive_response_is_discarded(app_env, monkeypatch):
    client, factory, _ = app_env
    sid = create(client)
    stalled(factory, sid)
    job = ingest(factory,sid,type='student_action',action='idle')
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


def test_observer_typing_cooldown_preferences_and_aggregation(monkeypatch):
    monkeypatch.setattr(utils,'call_llm',AsyncMock(side_effect=AssertionError('LLM forbidden')))
    state={'observer':{'failure_streak':3,'last_progress_at':0,'last_action_at':0,'problem_started_at':0},'proactive_enabled':True}
    decision,o,_=observer.observe(state,'student_action',{'action':'edit'},.5,observer.new_policy(),100)
    assert decision=='DEFER'
    decision,o,_=observer.observe({**state,'observer':o},'student_action',{'action':'idle'},.5,observer.new_policy(),105)
    assert decision=='WAKE'
    for update in ({'last_intervention_at':99},{'pending_wake':True},{'pending_intervention':{'at':99}}):
        decision,_,_=observer.observe({**state,'observer':{**state['observer'],**update}},'student_action',{'action':'idle'},.5,observer.new_policy(),100)
        assert decision=='WAIT'
    decision,_,_=observer.observe({**state,'proactive_enabled':False},'student_action',{},.5,observer.new_policy(),100)
    assert decision=='WAIT'


def test_learned_policy_can_choose_wait_and_receive_real_feedback(app_env):
    client,factory,_=app_env
    sid=create(client)
    stalled(factory,sid)
    policy=observer.new_policy();policy['updates']=20;policy['b'][0]=[100.]*10
    with factory.begin() as connection:
        row=connection.get(db.Session,sid)
        memory.update_long_term_memory(connection,row.user_id,{'policy':policy})
    job=ingest(factory,sid,type='student_action',action='idle')
    assert job['decision']=='WAIT'
    with factory.begin() as connection:
        row=connection.get(db.Session,sid);state=memory.get_short_term_memory(connection,sid)
        assert state['observer']['pending_decision']['action']=='WAIT'
        reflection.reflect_on_intervention(connection,sid,row.user_id,state,{'verified':True,'correct':True},job['event_id'])
        memory.update_short_term_memory(connection,sid,state)
    with factory() as connection:
        assert memory.get_long_term_memory(connection,row.user_id)['policy']['updates']==21


async def test_unverified_assessment_does_not_reward_bandit(app_env):
    client,factory,_=app_env
    sid=create(client);stalled(factory,sid)
    await orchestrator.handle(ingest(factory,sid,type='student_action',action='idle'))
    with factory.begin() as connection:
        row=connection.get(db.Session,sid);state=memory.get_short_term_memory(connection,sid)
        assert reflection.reflect_on_intervention(connection,sid,row.user_id,state,{'verified':False,'correct':True},1) is None
        assert memory.get_long_term_memory(connection,row.user_id)['policy']['updates']==0


async def test_feedback_is_linked_once_and_negative_outcome_replans(app_env):
    client,factory,_=app_env
    sid=create(client);stalled(factory,sid)
    output=(await orchestrator.handle(ingest(factory,sid,type='student_action',action='idle')))[0]
    with pytest.raises(ValueError):
        ingest(factory,sid,type='feedback',rating=1,intervention_id=999)
    job=ingest(factory,sid,type='feedback',rating=-1,intervention_id=output['event_id'])
    assert job['replan']
    assert (await orchestrator.handle(job))[0]['type']=='plan'
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
    assert client.get('/api/student').json()['memories']


async def test_real_sdk_responses_protocol_with_mock_transport(app_env,monkeypatch):
    """Exercise real call_llm plumbing and SDK-shaped parse/function outputs, no paid calls."""
    client,factory,_=app_env
    sid=create(client)
    monkeypatch.setattr(config,'DEMO_MODE',False)
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
        assert kwargs['tool_choice']=={'type':'function','name':'hint'}
        return SimpleNamespace(output=[SimpleNamespace(type='function_call',name='hint',arguments=json.dumps({'content':'Consider the left boundary.','concept':'Binary Search'}))],usage=usage)
    fake=SimpleNamespace(responses=SimpleNamespace(parse=AsyncMock(side_effect=parse),create=AsyncMock(side_effect=create_response)))
    monkeypatch.setattr(utils,'get_client',lambda:fake)
    output=await orchestrator.handle(ingest(factory,sid,type='student_message',content='Can I have a clue?'))
    assert output[0]['result']['content']=='Consider the left boundary.'
    assert utils.METRICS['llm_calls']==3 and utils.METRICS['output_tokens']==60
    assert fake.responses.parse.await_count==2 and fake.responses.create.await_count==1


async def test_observer_performance_and_metric_counts(app_env):
    policy=observer.new_policy()
    for _ in range(1000):
        observer.observe({'observer':{},'proactive_enabled':False},'student_action',{'action':'idle'},.5,policy)
    mean=utils.METRICS['observer_latency_ms']/utils.METRICS['observer_events']
    assert mean<20  # Broad regression bound, not a hardware benchmark.
    assert utils.METRICS['observer_wait']==1000


async def test_repeated_correct_answer_cannot_inflate_mastery(app_env):
    client,factory,_=app_env
    sid=create(client)
    for _ in range(3):
        await orchestrator.handle(ingest(factory,sid,type='problem_attempt',content='1',problem_id='binary-search-first'))
    assert client.get('/api/student').json()['mastery']['Binary Search']==.6


async def test_reasoning_assessment_is_not_exact_answer_check(app_env):
    client,factory,_=app_env
    sid=create(client)
    output=await orchestrator.handle(ingest(factory,sid,type='problem_attempt',content='1',problem_id='binary-search-first',assessment_mode='reasoning'))
    assert output[0]['result']['verified'] is False


def test_expired_observer_outcome_does_not_block_new_help():
    state={'observer':{'failure_streak':3,'last_progress_at':0,'last_action_at':0,
        'pending_intervention':{'at':1}},'proactive_enabled':True}
    action,o,_=observer.observe(state,'student_action',{'action':'idle'},.5,observer.new_policy(),1000)
    assert action=='WAKE' and 'pending_intervention' not in o


async def test_pre_intervention_attempt_is_not_credited_to_the_bandit(app_env):
    client,factory,_=app_env
    sid=create(client);stalled(factory,sid)
    job=ingest(factory,sid,type='student_action',action='idle')
    await orchestrator.handle(job)
    with factory.begin() as connection:
        row=connection.get(db.Session,sid);state=memory.get_short_term_memory(connection,sid)
        result=reflection.reflect_on_intervention(connection,sid,row.user_id,state,
            {'verified':True,'correct':True,'observed_at':job['received_at']-10},job['event_id'])
        assert result['reward'] is None
        assert memory.get_long_term_memory(connection,row.user_id)['policy']['updates']==0
