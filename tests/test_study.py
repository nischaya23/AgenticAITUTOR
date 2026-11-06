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
        issues=[dict(line=1, quote='Wrong boundary', reason='Check the update.')]), context)
    assert result['metadata']['start'] == len('😀 first\n')
    assert result['metadata']['issues'][0]['line'] == 2
    tools.validate_target(result, context)


def test_review_without_issues_still_validates():
    context = {'workspace': 'Reasoning here', 'knowledge': []}
    result = tools.reasoning_review(dict(content='No clear issues', concept='x', issues=[]), context)
    tools.validate_target(result, context)
