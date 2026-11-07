import asyncio
from unittest.mock import AsyncMock
import pytest
from backend import main, memory, knowledge, tools, orchestrator
from backend.db import Session


def prepare(client):
    material=client.post('/api/materials',files={'file':(
        'course.md',b'# Binary Search\nSorted binary search narrows an interval.','text/markdown')}).json()
    sid=client.post('/api/sessions').json()['id']
    response=client.post(f'/api/sessions/{sid}/study',json={'material_id':material['id']})
    assert response.status_code==200,response.text
    return sid,material,response.json()


def test_upload_starts_scoped_study_and_shows_unassessed_mastery(app_env):
    client,factory,_=app_env
    sid,material,study=prepare(client)
    assert study['state']['material_id']==material['id']
    assert 'expected_answer' not in study['state']['current_problem']
    assert [n['id'] for n in study['graph']['nodes']]==['Binary Search']
    assert study['graph']['nodes'][0]['mastery'] is None
    assert client.get(f'/api/sessions/{sid}/study').json()['material']['name']=='course.md'
    with factory() as db:
        context=orchestrator.context_for(db,sid)
        assert all(c['id'].startswith(material['id']) for c in context['knowledge'])
        assert context['prerequisites']==[]  # Seed graph links must not leak into this upload.


def test_correct_answers_advance_and_complete_automatically(app_env):
    client,factory,_=app_env
    sid,_,study=prepare(client)
    prompts=[]
    with client.websocket_connect(f'/ws/{sid}') as ws:
        for _ in range(4):
            old=study['state']['current_problem']['id']
            prompts.append(study['state']['current_problem']['prompt'])
            with factory() as db:
                expected=db.get(Session,sid).state['current_problem']['expected_answer']
            ws.send_json({'type':'problem_attempt','content':expected,'problem_id':old})
            while True:
                message=ws.receive_json()
                assert message['type']!='error',message
                if message['type']=='study_state' and (message['state'].get('study_complete') or
                    message['state']['current_problem']['id']!=old):
                    study=message;break
    assert study['state']['study_complete']
    assert study['graph']['nodes'][0]['mastery']>=study['mastery_target']
    assert len(set(prompts))==4


def test_wrong_answer_keeps_current_question_and_updates_mastery(app_env):
    client,_,_=app_env
    sid,_,study=prepare(client)
    old=study['state']['current_problem']['id']
    with client.websocket_connect(f'/ws/{sid}') as ws:
        ws.send_json({'type':'problem_attempt','content':'wrong','problem_id':old})
        while True:
            message=ws.receive_json()
            assert message['type']!='error',message
            if message['type']=='study_state':break
    assert message['state']['current_problem']['id']==old
    assert message['graph']['nodes'][0]['mastery']==.4


def test_graph_prerequisites_control_topic_selection():
    graph={'concepts':['Advanced','Basics'],'prerequisites':[{'prerequisite':'Basics','concept':'Advanced'}]}
    assert main.select_study_concept(graph,{})=='Basics'
    assert main.select_study_concept(graph,{'Basics':.8})=='Advanced'
    assert main.select_study_concept(graph,{'Basics':.8,'Advanced':.8}) is None


def test_generation_receives_only_the_uploaded_source(app_env,monkeypatch):
    client,_,_=app_env
    mock=AsyncMock(return_value={'id':'generated','prompt':'One exact question','expected_answer':'yes',
        'concept':'Binary Search','rubric':'Use the source.'})
    monkeypatch.setattr(tools,'generate_problem',mock)
    _,material,_=prepare(client)
    assert mock.await_count==1
    assert mock.call_args.kwargs['sources']
    assert all(c['id'].startswith(material['id']) for c in mock.call_args.kwargs['sources'])


async def test_new_question_is_rejected_if_work_changes_during_generation(app_env,monkeypatch):
    client,factory,_=app_env
    sid,_,study=prepare(client)
    entered,release=asyncio.Event(),asyncio.Event()
    original=tools.generate_problem
    async def slow(*args,**kwargs):
        entered.set();await release.wait();return await original(*args,**kwargs)
    monkeypatch.setattr(tools,'generate_problem',slow)
    task=asyncio.create_task(main.advance_study(sid))
    await asyncio.wait_for(entered.wait(),2)
    with factory.begin() as db:
        state=memory.get_short_term_memory(db,sid)
        memory.update_short_term_memory(db,sid,{'revision':state['revision']+1,'workspace':'Changed'})
    release.set()
    assert await task is None
    with factory() as db:
        assert db.get(Session,sid).state['current_problem']['id']==study['state']['current_problem']['id']


def test_restarting_study_resumes_a_solved_question_without_duplicate_grading(app_env):
    client,factory,_=app_env
    sid,material,study=prepare(client)
    with factory.begin() as db:
        state=memory.get_short_term_memory(db,sid)
        state['current_problem']['solved']=True
        memory.update_short_term_memory(db,sid,state)
    resumed=client.post(f'/api/sessions/{sid}/study',json={'material_id':material['id']})
    assert resumed.status_code==200
    assert resumed.json()['state']['current_problem']['id']!=study['state']['current_problem']['id']
    assert resumed.json()['graph']['nodes'][0]['mastery'] is None


def test_skip_changes_question_without_mastery_credit(app_env):
    client,factory,_=app_env
    sid,_,study=prepare(client)
    old=study['state']['current_problem']['id']
    response=client.post(f'/api/sessions/{sid}/skip',json={'problem_id':old})
    assert response.status_code==200,response.text
    assert response.json()['state']['current_problem']['id']!=old
    assert response.json()['graph']['nodes'][0]['mastery'] is None
    assert client.post(f'/api/sessions/{sid}/skip',json={'problem_id':old}).status_code==409


def test_reasoning_issue_requires_exact_current_line():
    result=tools.highlight(dict(content='Check this step',concept='Binary Search',target='workspace',
        source_id='workspace',quote='wrong step',start=0,end=10,
        issues=[dict(line=1,quote='wrong step',reason='Check the interval boundary.')]))
    tools.validate_target(result,{'workspace':'wrong step','knowledge':[]})
    with pytest.raises(ValueError):
        tools.validate_target(result,{'workspace':'new step','knowledge':[]})


def test_review_derives_unicode_offsets_and_resolves_unique_line():
    context = {'workspace': '😀 first\nWrong boundary\nlast', 'knowledge': []}
    result = tools.reasoning_review(dict(content='Check boundary', concept='Binary Search',
        issues=[dict(line=1, quote='Wrong boundary', reason='Check the update.', category='possible_error')]), context)
    assert result['metadata']['annotations'][0]['start'] == len('😀 first\n')
    assert result['metadata']['annotations'][0]['line'] == 2
    tools.validate_target(result, context)


def test_review_without_issues_still_validates():
    context = {'workspace': 'Reasoning here', 'knowledge': []}
    result = tools.reasoning_review(dict(content='No clear issues', concept='x', issues=[]), context)
    tools.validate_target(result, context)


async def test_live_grading_calls_llm_even_for_exact_answer(monkeypatch):
    from backend import assessment, config, utils
    monkeypatch.setattr(config, 'DEMO_MODE', False)
    mock = AsyncMock(return_value=assessment.Assessment(correct=True,score=1,
        reason='Equivalent answer accepted.',misconceptions=[]))
    monkeypatch.setattr(utils, 'call_llm', mock)
    problem = dict(prompt='What is two plus two?',expected_answer='4',rubric='Accept four.')
    context = dict(workspace='2+2', student={'memories':['Practicing arithmetic']},knowledge=[])
    result = await assessment.evaluate_attempt(problem, 'four', context)
    assert result['correct'] and result['method']=='llm'
    assert result['verified'] is False
    assert mock.call_args.args[1] == dict(problem=problem,answer='four',context=context)


def test_memory_activity_records_operations_without_contents(app_env):
    from backend import activity
    client,factory,_=app_env
    sid,_,_=prepare(client)
    items=[]
    token=activity.CURRENT.set(items)
    try:
        with factory.begin() as db:
            row=db.get(Session,sid)
            memory.get_short_term_memory(db,sid)
            memory.get_long_term_memory(db,row.user_id)
            memory.get_relevant_history(db,sid)
            memory.update_long_term_memory(db,row.user_id,{'memories':['private learning detail']})
    finally:
        activity.CURRENT.reset(token)
    assert any(i['kind']=='memory_read' for i in items)
    assert any(i['kind']=='memory_update' for i in items)
    assert 'private learning detail' not in str(items)


def test_routine_events_do_not_emit_memory_activity(app_env):
    client,_,_=app_env
    sid,_,_=prepare(client)
    with client.websocket_connect(f'/ws/{sid}') as socket:
        assert socket.receive_json()['type']=='observer_status'
        for action in ('edit','idle'):
            socket.send_json({'type':'student_action','action':action,'content':'My reasoning'})
            messages=[]
            while True:
                message=socket.receive_json()
                messages.append(message)
                if message['type']=='observer_status':
                    break
            assert all(m['type']!='memory_activity' for m in messages)


async def test_grading_groups_activity_and_persists_reflection(app_env):
    client,factory,_=app_env
    sid,_,study=prepare(client)
    from backend import utils
    with factory.begin() as db:
        job=orchestrator.ingest(db,sid,utils.ClientEvent(type='problem_attempt',content='1',
            problem_id=study['state']['current_problem']['id']))
    outputs=await orchestrator.handle(job)
    activity=[o for o in outputs if o['type']=='memory_activity']
    assert len(activity)==1
    assert len(activity[0]['items'])==1
    assert activity[0]['items'][0]['operation']=='Answer assessment'
    with factory() as db:
        model=memory.get_long_term_memory(db,db.get(Session,sid).user_id)
        assert model['memories'] and model['learning_goals']


@pytest.mark.parametrize('correct', [False, True])
async def test_llm_decision_controls_next_question(app_env, monkeypatch, correct):
    from backend import assessment, reflection, utils
    client,factory,_=app_env
    sid,_,study=prepare(client)
    old=study['state']['current_problem']['id']
    monkeypatch.setattr(assessment,'evaluate_attempt',AsyncMock(return_value=dict(
        correct=correct,score=float(correct),reason='Model feedback.',misconceptions=[],verified=False,method='llm')))
    monkeypatch.setattr(reflection,'reflect_on_answer',AsyncMock(return_value=dict(
        summary='Practice interval boundaries.',next_goal='Check earlier matches.')))
    with factory.begin() as db:
        job=orchestrator.ingest(db,sid,utils.ClientEvent(type='problem_attempt',content='answer',problem_id=old))
    await main.run_job(job)
    with factory() as db:
        state=db.get(Session,sid).state
        assert (state['current_problem']['id']!=old)==correct
        if not correct:
            assert not state['current_problem'].get('solved')
