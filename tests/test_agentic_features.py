import asyncio
import json
import shutil
import subprocess
import time
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from backend import config, context, features, memory, operations, orchestrator, research, utils, video, websearch
from backend.db import Session, Operation, StudentState, User
from backend.drawing_script import DrawingScript
from backend.observer_review import Decision, ReviewScheduler
from test_study import prepare


def wait_job(client,oid):
    for _ in range(100):
        value=client.get('/api/operations/'+oid).json()
        if value['status']!='running': return value
        time.sleep(.02)
    raise AssertionError('Preparation did not finish.')


def test_memory_corrections_persist_and_feed_context(app_env):
    client,factory,_=app_env
    sid,_,_=prepare(client)
    with factory.begin() as db:
        memory.update_long_term_memory(db,1,dict(memories=['Original summary'],learning_goals=['Agent goal'],
            misconceptions=[dict(concept='Binary Search',hypothesis='Confuses boundaries',evidence_event_id=1,status='unconfirmed')]))
    snapshot=client.get(f'/api/sessions/{sid}/memory').json()
    assert 'expected_answer' not in json.dumps(snapshot) and 'policy' not in json.dumps(snapshot)
    summary=next(i for i in snapshot['items'] if i['kind']=='summary')
    correction=dict(expected_revision=snapshot['revision'],action='edit',item_id=summary['id'],text='Corrected summary')
    response=client.post(f'/api/sessions/{sid}/memory/corrections',json=correction)
    assert response.status_code==200,response.text
    assert client.post(f'/api/sessions/{sid}/memory/corrections',json=correction).status_code==409
    snapshot=response.json()
    response=client.post(f'/api/sessions/{sid}/memory/corrections',json=dict(expected_revision=snapshot['revision'],action='add_goal',text='My own priority'))
    snapshot=response.json()
    misconception=next(i for i in snapshot['items'] if i['kind']=='misconception')
    response=client.post(f'/api/sessions/{sid}/memory/corrections',json=dict(expected_revision=snapshot['revision'],action='dispute',item_id=misconception['id'],text='I meant the inclusive boundary.'))
    assert response.status_code==200,response.text
    with factory.begin() as db:
        memory.update_long_term_memory(db,1,{'learning_goals':['A newer agent goal']})
        learning=context.for_session(db,sid)
        assert learning['student']['memories']==['Corrected summary']
        assert learning['student']['student_goals']==['My own priority']
        assert learning['student']['misconceptions'][0]['status']=='disputed'
        assert 'inclusive boundary' in learning['student']['misconceptions'][0]['student_note']
    snapshot=client.get(f'/api/sessions/{sid}/memory').json()
    edited=next(i for i in snapshot['items'] if i['id']==summary['id'])
    assert edited['original_text']=='Original summary' and edited['edits']


def test_topic_job_research_graph_progress_and_ownership(app_env,monkeypatch):
    client,_,_=app_env
    async def search(query,max_pages=None):
        return [dict(title='Binary Search',url='https://example.org/search',text='# Binary Search\nSorted search halves the candidate interval.')]
    monkeypatch.setattr(websearch,'search',search)
    response=client.post('/api/material-jobs/topic',json={'description':'Teach me binary search from the beginning.'})
    assert response.status_code==202
    oid=response.json()['id'];job=wait_job(client,oid)
    assert job['status']=='ready',job
    assert job['events'][0]['kind']=='interpreting_topic'
    assert any(e['kind']=='selected' and e['url']=='https://example.org/search' for e in job['events'])
    material_id=job['result']['id']
    sid=client.post('/api/sessions').json()['id']
    study=client.post(f'/api/sessions/{sid}/study',json={'material_id':material_id})
    assert study.status_code==200,study.text
    assert study.json()['material']['origin']=='topic'
    assert study.json()['material']['graph']['evidence']
    stream=client.get(f'/api/operations/{oid}/events?after=0')
    assert 'text/event-stream' in stream.headers['content-type'] and 'selected' in stream.text
    last=job['events'][-1]['id']
    assert 'data:' not in client.get(f'/api/operations/{oid}/events?after={last}').text
    client.post('/api/auth/signup',json={'email':'other@local.test','password':'test-password'})
    assert client.get('/api/operations/'+oid).status_code==404
    assert client.get(f'/api/sessions/{sid}/memory').status_code==404


def test_topic_ambiguity_waits_for_ui_choice(app_env,monkeypatch):
    client,_,_=app_env
    async def interpret(topic,learning_context=None):
        return [websearch.Meaning(label='Computing',query='RAG AI',description='Retrieval augmented generation'),
                websearch.Meaning(label='Textiles',query='rag cloth',description='Fabric')]
    calls=[]
    async def search(query,max_pages=None):
        calls.append(query)
        return [dict(title='Computing',url='https://example.org/rag',text='# Computing\nGround answers in retrieved passages.')]
    monkeypatch.setattr(websearch,'interpret_topic',interpret);monkeypatch.setattr(websearch,'search',search)
    oid=client.post('/api/material-jobs/topic',json={'description':'RAG'}).json()['id']
    assert wait_job(client,oid)['status']=='needs_clarification'
    assert not calls
    assert client.post(f'/api/material-jobs/{oid}/meaning',json={'index':99}).status_code==422
    assert client.post(f'/api/material-jobs/{oid}/meaning',json={'index':0}).status_code==202
    assert wait_job(client,oid)['status']=='ready'
    assert calls


def test_upload_research_failure_keeps_upload_and_text_contract(app_env):
    client,_,_=app_env
    oid=client.post('/api/material-jobs',files={'files':('notes.md',b'# Search\nAn interval shrinks each step.','text/markdown')}).json()['id']
    job=wait_job(client,oid)
    assert job['status']=='ready',job
    sid=client.post('/api/sessions').json()['id']
    study=client.post(f'/api/sessions/{sid}/study',json={'material_id':job['result']['id']}).json()
    assert set(study['state']['response'])=={'text'}
    assert 'diagram' not in study['state']['current_problem']
    with client.websocket_connect(f'/ws/{sid}') as socket:
        socket.receive_json()
        socket.send_json(dict(type='problem_attempt',response={'text':'Answer','diagram':{'nodes':[],'edges':[]}},problem_id=study['state']['current_problem']['id']))
        assert socket.receive_json()['type']=='error'


def test_upload_reports_provider_unavailability(app_env, monkeypatch):
    import httpx
    from openai import InternalServerError
    from backend import knowledge

    client, _, _ = app_env
    monkeypatch.setattr(config, 'LLM_PROVIDER', 'gemini')
    monkeypatch.setattr(config, 'WEB_SEARCH_ON_UPLOAD', False)
    response = httpx.Response(503, request=httpx.Request('POST', 'https://example.test'))
    monkeypatch.setattr(knowledge, 'extract_concepts', AsyncMock(side_effect=InternalServerError(
        'provider error', response=response, body={'error': {'status': 'UNAVAILABLE'}})))
    upload = client.post('/api/material-jobs', files={'files': ('notes.md', b'# Search\nNotes.', 'text/markdown')})
    job = wait_job(client, upload.json()['id'])
    assert job['status'] == 'failed'
    assert job['error'] == 'Gemini is temporarily unavailable (HTTP 503). Try again shortly.'


async def test_research_uses_native_tool_results_and_respects_budget(monkeypatch):
    calls=[]
    async def model(instructions,data,**kwargs):
        calls.append(kwargs)
        name='web_search' if len(calls)==1 else 'finish_research'
        args=dict(query='binary search',subject='Computer science',purpose='Find an explanation') if len(calls)==1 else dict(reason='Enough evidence')
        return SimpleNamespace(output=[SimpleNamespace(type='function_call',call_id=f'call{len(calls)}',name=name,arguments=json.dumps(args))])
    monkeypatch.setattr(utils,'call_llm',model)
    monkeypatch.setattr(websearch,'search',AsyncMock(return_value=[dict(title='Search',url='https://example.org/search',text='Search interval')]))
    monkeypatch.setattr(websearch,'relevant_pages',AsyncMock(side_effect=lambda pages,subject:pages))
    pages=await research.investigate({'subject':'Search'},required=True,budget=2)
    assert pages and len(calls)==2
    assert calls[1]['tool_history'][0]['call_id']=='call1'
    assert calls[1]['tool_history'][0]['result'][0]['url']=='https://example.org/search'


@pytest.mark.parametrize('action', ['WAIT', 'WAKE'])
async def test_observer_decides_on_idle_even_after_recent_help(app_env, monkeypatch, action):
    client, factory, _ = app_env
    sid, _, _ = prepare(client)
    monkeypatch.setattr(config, 'OBSERVER_LLM_ENABLED', True)
    monkeypatch.setattr(config, 'IDLE_SECONDS', 60)
    now = 1000
    monkeypatch.setattr(utils, 'now', lambda: now)
    with factory.begin() as db:
        state = memory.get_short_term_memory(db, sid)
        state['observer'].update(last_action_at=now - 59, last_intervention_at=now - 2)
        memory.update_short_term_memory(db, sid, state)
        job = orchestrator.ingest(db, sid, utils.ClientEvent(type='student_action', action='idle'))
    model = AsyncMock(return_value=Decision(action=action, reason='Reviewed the current work.', evidence_references=[]))
    monkeypatch.setattr(utils, 'call_llm', model)
    run = AsyncMock()
    scheduler = ReviewScheduler(factory, AsyncMock(), lambda *_: None, run)
    scheduler.schedule(sid, job)
    await scheduler.tasks[sid]
    model.assert_not_awaited()
    now += 1
    scheduler.schedule(sid, job)
    await scheduler.tasks[sid]
    assert model.call_args.args[1]['trigger'] == 'idle'
    assert model.call_args.args[1]['activity']['seconds_since_help'] == 3
    with factory() as db:
        assert db.get(Session, sid).state['observer']['status']['decision'] == action
    assert run.await_count == (action == 'WAKE')
    if action == 'WAKE':
        assert run.call_args.args[0]['observer_approved']


@pytest.mark.parametrize('routine_overdue', [False, True])
async def test_committed_incorrect_assessment_takes_priority(app_env, monkeypatch, routine_overdue):
    client, factory, _ = app_env
    sid, _, study = prepare(client)
    monkeypatch.setattr(config, 'OBSERVER_LLM_ENABLED', True)
    with factory.begin() as db:
        job = orchestrator.ingest(db, sid, utils.ClientEvent(
            type='problem_attempt', content='wrong', problem_id=study['state']['current_problem']['id']))
    await orchestrator.handle(job)
    model = AsyncMock(return_value=Decision(action='WAIT', reason='Let the student apply the feedback.', evidence_references=[]))
    monkeypatch.setattr(utils, 'call_llm', model)
    scheduler = ReviewScheduler(factory, AsyncMock(), lambda *_: None, AsyncMock())
    now = utils.now()
    scheduler.last_started[sid] = now - config.OBSERVER_REVIEW_SECONDS - 1 if routine_overdue else now
    with factory.begin() as db:
        state = memory.get_short_term_memory(db, sid)
        state['observer']['last_edit_at'] = now - config.OBSERVER_PAUSE_SECONDS - 1
        memory.update_short_term_memory(db, sid, state)
    edit = {**job, 'kind': 'student_action', 'action': 'edit', 'draft_changed': True}
    edit.pop('incorrect_assessment_id')
    scheduler.schedule(sid, edit)
    scheduler.schedule(sid, job)
    await asyncio.wait_for(scheduler.tasks[sid], 2)
    assert model.call_args.args[1]['trigger'] == 'incorrect_assessment'
    assert model.call_args.args[1]['activity']['last_assessment']['correct'] is False
    assert model.await_count == 1
    scheduler.run_job.assert_not_awaited()


@pytest.mark.parametrize('scenario', ['disabled', 'stale', 'failure', 'invalid'])
async def test_observer_does_not_wake_without_a_fresh_valid_decision(app_env, monkeypatch, scenario):
    client, factory, _ = app_env
    sid, _, _ = prepare(client)
    monkeypatch.setattr(config, 'OBSERVER_LLM_ENABLED', True)
    monkeypatch.setattr(config, 'IDLE_SECONDS', 0)
    with factory.begin() as db:
        if scenario == 'disabled':
            memory.update_short_term_memory(db, sid, {'proactive_enabled': False})
        job = orchestrator.ingest(db, sid, utils.ClientEvent(type='student_action', action='idle'))

    async def decide(*args, **kwargs):
        if scenario == 'failure':
            raise RuntimeError('provider unavailable')
        if scenario == 'invalid':
            return {'action': 'WAKE', 'reason': 'Needs help.', 'evidence_references': ['event:missing']}
        with factory.begin() as db:
            orchestrator.ingest(db, sid, utils.ClientEvent(type='student_action', action='edit', content='New work'))
        return Decision(action='WAKE', reason='Needs help.', evidence_references=[])

    model = AsyncMock(side_effect=decide)
    monkeypatch.setattr(utils, 'call_llm', model)
    run = AsyncMock()
    publish = AsyncMock()
    scheduler = ReviewScheduler(factory, publish, lambda *_: None, run)
    scheduler.schedule(sid, job)
    await scheduler.tasks[sid]
    run.assert_not_awaited()
    if scenario == 'disabled':
        model.assert_not_awaited()
    if scenario in ('disabled', 'stale'):
        publish.assert_not_awaited()
    else:
        assert publish.call_args.args[1]['decision'] == 'UNAVAILABLE'


def test_drawing_interpreter_blocks_execution_and_bounds_work():
    for source in ["import os\ndef draw_frame(canvas,t):\n    os.system('id')", "def draw_frame(canvas,t):\n    open('/tmp/x','w')", "def draw_frame(canvas,t):\n    canvas.draw.text('escape')", "def draw_frame(canvas,t):\n    while True: pass"]:
        with pytest.raises(ValueError): DrawingScript(source)
    program=DrawingScript('def draw_frame(canvas, t):\n    x = [1] * 100000000')
    with pytest.raises(ValueError): program.draw(None,0)
    program=DrawingScript('def draw_frame(canvas, t):\n    for x in range(2000):\n        for y in range(2000):\n            z = x + y')
    with pytest.raises(ValueError,match='budget'): program.draw(None,0)


async def test_local_video_render_codec_cache_and_account_scoping(tmp_path,monkeypatch):
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'): pytest.skip('FFmpeg is required for renderer integration.')
    monkeypatch.setattr(config,'DATA_DIR',tmp_path)
    spec=video.VideoScript(title='Interval',content='Shrink the interval.',concept='Search',duration=2,
        code="def draw_frame(canvas, t):\n    canvas.text(40, 40, 'Narrow the interval')\n    canvas.circle(canvas.lerp(100, 800, t / 2), 220, 20)",
        chapters=[dict(start=0,end=2,text='Narrow the interval.')])
    key=await video.render(spec,1)
    path=video.directory(1)/key/'video.mp4'
    metadata=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','stream=codec_name,width,height,pix_fmt','-show_entries','format=duration','-of','json',str(path)]))
    assert metadata['streams'][0]==dict(codec_name='h264',width=960,height=540,pix_fmt='yuv420p')
    assert float(metadata['format']['duration'])==2
    changed=path.stat().st_mtime_ns
    assert await video.render(spec,1)==key and path.stat().st_mtime_ns==changed
    assert not (video.directory(2)/key/'video.mp4').exists()


@pytest.mark.parametrize('provider',['openai','gemini'])
async def test_provider_adapters_preserve_native_tool_history(monkeypatch,provider):
    from conftest import REAL_CALL_LLM
    monkeypatch.setattr(config,'LLM_PROVIDER',provider)
    monkeypatch.setattr(config,'GEMINI_MODEL','test-model')
    create=AsyncMock(return_value=SimpleNamespace(usage=None,output=[],choices=[SimpleNamespace(
        finish_reason='stop',message=SimpleNamespace(content=None,tool_calls=[SimpleNamespace(id='next',type='function',
            function=SimpleNamespace(name='finish_research',arguments='{"reason":"Enough"}'))]))]))
    monkeypatch.setattr(utils,'get_client',lambda:SimpleNamespace(responses=SimpleNamespace(create=create),chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    turn=dict(call_id='search-1',name='web_search',arguments='{"query":"Search"}',result=[{'url':'https://example.org'}])
    await REAL_CALL_LLM('Research','Search',tools=[research.definition(research.FinishArgs,'finish_research','Finish.')],tool_choice='required',tool_history=[turn])
    args=create.call_args.kwargs
    if provider=='openai':
        assert args['input'][1]['call_id']=='search-1'
        assert args['input'][2]['type']=='function_call_output'
    else:
        assert args['messages'][2]['tool_calls'][0]['id']=='search-1'
        assert args['messages'][3]['tool_call_id']=='search-1'


def test_visualization_job_delivers_seekable_video_and_captions(app_env):
    if not shutil.which('ffmpeg'): pytest.skip('FFmpeg required.')
    client,_,_=app_env
    sid,_,_=prepare(client)
    with client.websocket_connect(f'/ws/{sid}') as socket:
        socket.receive_json()
        socket.send_json(dict(type='student_action',action='hint_request',tool='visualization'))
        pending=None; delivered=None
        for _ in range(30):
            message=socket.receive_json()
            if message['type']=='error': pytest.fail(message['message'])
            if message['type']=='video_pending': pending=message
            if message['type']=='intervention': delivered=message;break
        assert pending and delivered
        url=delivered['result']['metadata']['video']['url']
        response=client.get(url,headers={'Range':'bytes=0-99'})
        assert response.status_code==206 and len(response.content)==100
        captions=client.get(url+'/captions')
        assert captions.status_code==200 and captions.text.startswith('WEBVTT')
        assert 'earlier match' in captions.text
        job=wait_job(client,pending['operation_id'])
        assert job['status']=='ready' and 'retry_data' not in job
    client.post('/api/auth/signup',json={'email':'video-other@local.test','password':'test-password'})
    assert client.get(url).status_code==404
    assert client.get(url+'/captions').status_code==404


async def test_observer_coalesces_drafts_after_quiet_period_and_limits_review_rate(app_env,monkeypatch):
    from test_response_hypotheses import Clock
    client,factory,_=app_env
    sid,_,_=prepare(client)
    clock=Clock();monkeypatch.setattr(utils,'now',lambda:clock.time)
    monkeypatch.setattr(config,'OBSERVER_LLM_ENABLED',True)
    monkeypatch.setattr(config,'OBSERVER_PAUSE_SECONDS',8)
    monkeypatch.setattr(config,'OBSERVER_REVIEW_SECONDS',30)
    model=AsyncMock(return_value=Decision(action='WAIT',reason='The student is progressing.',evidence_references=[]))
    monkeypatch.setattr(utils,'call_llm',model)
    busy = True
    scheduler=ReviewScheduler(factory,AsyncMock(),lambda *_:None,AsyncMock(),busy=lambda _: busy)
    scheduler.sleep=clock.sleep
    def edit(text):
        with factory.begin() as db:
            return orchestrator.ingest(db,sid,utils.ClientEvent(type='student_action',action='edit',content=text))
    scheduler.schedule(sid,edit('First draft'))
    await clock.advance(4)
    scheduler.schedule(sid,edit('Latest draft'))
    await clock.advance(4)
    assert model.await_count==0
    await clock.advance(4)
    assert model.await_count==0
    busy = False
    await clock.advance(1)
    assert model.await_count==1
    assert model.call_args.args[1]['context']['response']['text']=='Latest draft'
    scheduler.schedule(sid,edit('New reasoning'))
    await clock.advance(8)
    assert model.await_count==1
    await clock.advance(22)
    assert model.await_count==2
    assert model.call_args.args[1]['context']['response']['text']=='New reasoning'
