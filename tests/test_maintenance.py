import asyncio
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID
import pytest
from backend import config, main, utils, prompts, logging_setup as logs


def test_new_session_is_empty_and_log_uuid_stable(app_env):
    client,factory,_=app_env
    first=client.post('/api/sessions').json()
    assert not first['state']['current_problem'] and not first['state']['current_concept']
    assert 'log_uuid' not in first['state']
    from backend.db import Session
    with factory() as db:
        uid=db.get(Session,first['id']).state['log_uuid']
        UUID(uid)
    assert main.session_log_uuid(first['id'])==uid
    with factory.begin() as db:
        row=db.get(Session,first['id'])
        row.state={k:v for k,v in row.state.items() if k!='log_uuid'}
    legacy=main.session_log_uuid(first['id'])
    assert main.session_log_uuid(first['id'])==legacy
    assert 'demo_mode' not in client.get('/api/health').json()
    assert client.get('/api/materials').json()==[]
    with client.websocket_connect(f"/ws/{first['id']}") as ws:
        ws.receive_json()
        ws.send_json(dict(type='problem_attempt',content='1'))
        assert ws.receive_json()['type']=='error'


async def test_session_logs_isolate_async_and_thread_context(tmp_path):
    logs.setup(tmp_path)
    logs.setup(tmp_path)
    try:
        async def write(sid,uid):
            with logs.scope(sid,uid):
                await asyncio.sleep(0)
                await asyncio.to_thread(logs.event,'worker.done',data='line1\nline2')
        await asyncio.gather(write(1,'a'*32),write(2,'b'*32))
    finally:
        logs.shutdown()
    for sid,key in [(1,'a'),(2,'b')]:
        records=[json.loads(x) for x in (tmp_path/(key*32+'.log')).read_text().splitlines()]
        assert len(records)==1
        assert records[0]['session_id']==sid
        assert records[0]['operation_id']!='-'
        assert records[0]['timestamp'].endswith('+00:00')
    assert len(list(tmp_path.glob('startup-*.log')))==1


@pytest.mark.parametrize('provider',['openai','gemini'])
async def test_real_adapter_logs_payload_without_client_credentials(tmp_path,monkeypatch,provider):
    monkeypatch.setattr(config,'LLM_PROVIDER',provider)
    monkeypatch.setattr(config,'GEMINI_MODEL','test-model')
    usage=SimpleNamespace(input_tokens=1,output_tokens=2,prompt_tokens=1,completion_tokens=2)
    response=SimpleNamespace(usage=usage,output_text='answer',choices=[SimpleNamespace(
        finish_reason='stop',message=SimpleNamespace(content='answer'))])
    create=AsyncMock(return_value=response)
    client=SimpleNamespace(api_key='DO-NOT-LOG',responses=SimpleNamespace(create=create),
                           chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(utils,'get_client',lambda:client)
    logs.setup(tmp_path)
    try:
        with logs.scope(1,'c'*32):
            assert await utils.call_llm(prompts.ASSESS_ANSWER,{'answer':'private answer'},operation='ASSESS_ANSWER')=='answer'
    finally:
        logs.shutdown()
    text=(tmp_path/('c'*32+'.log')).read_text()
    assert 'private answer' in text and 'ASSESS_ANSWER' in text and 'answer' in text
    assert 'DO-NOT-LOG' not in text
    assert 'llm.response' in text and 'duration_ms' in text


def test_model_call_sites_use_prompt_catalog():
    for name in ['planner','tutor','tools','reflection','assessment','knowledge']:
        tree=ast.parse(Path(f'backend/{name}.py').read_text())
        for node in ast.walk(tree):
            if isinstance(node,ast.Call) and ast.unparse(node.func)=='utils.call_llm':
                assert not isinstance(node.args[0],ast.Constant), name
                assert any(k.arg=='operation' for k in node.keywords),name
    assert not hasattr(config,'DEMO_MODE')


async def test_provider_error_is_logged_without_claiming_response(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'LLM_PROVIDER','openai')
    monkeypatch.setattr(utils,'get_client',lambda:SimpleNamespace(responses=SimpleNamespace(
        create=AsyncMock(side_effect=ValueError('bad output')))))
    logs.setup(tmp_path)
    try:
        with logs.scope(3,'d'*32):
            with pytest.raises(ValueError):
                await utils.call_llm('instructions','input',operation='failure-test')
    finally:
        logs.shutdown()
    text=(tmp_path/('d'*32+'.log')).read_text()
    assert 'llm.failed' in text and 'llm.response' not in text
