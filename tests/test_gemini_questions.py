"""Exercise the real compatibility SDK with a mocked HTTP transport."""
import json
import warnings
from unittest.mock import AsyncMock
import httpx
import pytest
from openai import AsyncOpenAI, BadRequestError, APITimeoutError, InternalServerError
from openai.types.chat.parsed_chat_completion import ParsedChatCompletionMessage
from backend import config, logging_setup as logs, planner, tools, utils


def diagram():
    return dict(nodes=[dict(id='a', label='Input', position=dict(x=0, y=0)),
                       dict(id='b', label='Output', position=dict(x=200, y=0))],
                edges=[dict(id='ab', source='a', target='b')])


def wire_question(format):
    return tools.GeminiProblem(prompt='Explain or draw the transformation.', concept='Flow',
        expected_answer='A valid transformation.', rubric='Check the direction and labels.',
        format=format, diagnostic_focus='Explain directed flow.',
        diagram_json=json.dumps(diagram()) if format == 'diagram_interpretation' else '',
        starter_diagram_json=json.dumps(diagram()) if format == 'diagram_construction' else '')


@pytest.mark.parametrize('format', ['explanation', 'trace', 'diagram_interpretation', 'diagram_construction'])
async def test_gemini_question_uses_shallow_schema_and_normalizes_diagrams(monkeypatch, format):
    wire = wire_question(format)
    async def transport(request):
        body = json.loads(request.content)
        schema = body['response_format']['json_schema']['schema']
        assert '$defs' not in schema
        assert all(prop['type'] == 'string' for prop in schema['properties'].values())
        assert 'diagram_json' in schema['properties'] and 'diagram' not in schema['properties']
        assert 'diagram_json' in body['messages'][0]['content']
        return httpx.Response(200, json=dict(id='test', created=1, model='test-model', object='chat.completion',
            choices=[dict(index=0, finish_reason='stop', message=dict(role='assistant', content=wire.model_dump_json()))],
            usage=dict(prompt_tokens=10, completion_tokens=10, total_tokens=20)))
    async with AsyncOpenAI(api_key='test-key', base_url='https://example.test/v1/',
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(transport))) as client:
        monkeypatch.setattr(utils, 'get_client', lambda: client)
        monkeypatch.setattr(config, 'LLM_PROVIDER', 'gemini')
        monkeypatch.setattr(config, 'GEMINI_MODEL', 'test-model')
        with warnings.catch_warnings():
            warnings.simplefilter('error', UserWarning)
            result = await tools.generate_problem('Flow', sources=[])
    assert result['format'] == format
    assert result['expected_answer'] == wire.expected_answer
    assert result['diagram'] == (diagram() if format == 'diagram_interpretation' else None)
    assert result['starter_diagram'] == (diagram() if format == 'diagram_construction' else None)
    assert 'diagram_json' not in result and 'starter_diagram_json' not in result


@pytest.mark.parametrize('invalid', ['json', 'endpoint', 'positions', 'node_limit', 'missing_interpretation'])
def test_gemini_wire_diagrams_keep_full_local_validation(invalid):
    wire = wire_question('diagram_interpretation')
    value = diagram()
    if invalid == 'json': wire.diagram_json = 'not JSON'
    elif invalid == 'missing_interpretation': wire.diagram_json = ''
    else:
        if invalid == 'endpoint': value['edges'][0]['target'] = 'missing'
        if invalid == 'positions': value['nodes'][0]['position']['x'] = float('inf')
        if invalid == 'node_limit': value['nodes'] *= 16
        wire.diagram_json = json.dumps(value)
    with pytest.raises(ValueError): wire.to_problem()


def test_runtime_parsed_models_log_without_serializer_warnings():
    plan = planner.Plan(objective='Learn', steps=['Practice'], learning_goals=[])
    message = ParsedChatCompletionMessage[None](role='assistant', content='{}', parsed=None)
    message.parsed = plan
    with warnings.catch_warnings():
        warnings.simplefilter('error', UserWarning)
        payload = logs.serializable(message)
        logs.event('llm.response', response=message)
    assert payload['parsed'] == plan.model_dump()


@pytest.mark.parametrize('failure,status', [('bad_request',502), ('rate_limit',503), ('unavailable',503), ('timeout',504)])
def test_provider_failure_during_study_preserves_plan_and_returns_retryable_json(app_env, monkeypatch, failure, status):
    client, factory, _ = app_env
    material = client.post('/api/materials', files={'file': ('notes.md', b'# Flow\nInput becomes output.','text/markdown')}).json()
    sid = client.post('/api/sessions').json()['id']
    request = httpx.Request('POST', 'https://example.test/completions')
    if failure == 'timeout':
        error = APITimeoutError(request=request)
    else:
        from openai import RateLimitError
        cls, code = (RateLimitError, 429) if failure == 'rate_limit' else (InternalServerError, 503) if failure == 'unavailable' else (BadRequestError, 400)
        error = cls('Provider details stay private.', response=httpx.Response(code, request=request), body={})
    original = tools.generate_problem
    monkeypatch.setattr(tools, 'generate_problem', AsyncMock(side_effect=error))
    response = client.post(f'/api/sessions/{sid}/study', json={'material_id': material['id']})
    assert response.status_code == status, response.text
    assert 'retry' in response.json()['detail'].lower()
    assert 'Provider details stay private' not in response.text
    state = client.get(f'/api/sessions/{sid}').json()['state']
    assert state['material_id'] == material['id'] and state['plan_version'] == 1
    assert state['plan']['steps'] and not state['current_problem']
    monkeypatch.setattr(tools, 'generate_problem', original)
    retry = client.post(f'/api/sessions/{sid}/study', json={'material_id': material['id']})
    assert retry.status_code == 200, retry.text
    assert retry.json()['state']['plan'] == state['plan']
    assert retry.json()['state']['plan_version'] == state['plan_version']
