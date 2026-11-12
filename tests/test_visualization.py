import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from backend import config, tools, tutor, utils
from model_fixtures import tool_arguments

def test_annotation_resolves_substrings_and_rejects_ambiguous_occurrences():
    args = {'content': 'Revisit this line.', 'concept': 'Binary Search', 'issues': [
        {'line': 1, 'quote': 'mid', 'reason': 'Justify your choice.', 'category': 'missing_justification'},
        {'line': 2, 'quote': 'left', 'reason': 'State the boundary invariant.', 'category': 'important_concept'},
    ]}
    context = {'workspace': '😀 use mid\nupdate left'}
    result = tools.reasoning_review(args, context)
    tools.validate_target(result, context)
    assert [item['start'] for item in result['metadata']['annotations']] == [6, 17]
    with pytest.raises(ValueError, match='ambiguous'):
        tools.reasoning_review(args, {'workspace': '😀 use mid or mid\nupdate left'})
    with pytest.raises(ValueError, match='current reasoning'):
        tools.validate_target(result, {'workspace': 'replace that step\nupdate left'})


async def test_gemini_slideshow_repairs_an_invalid_node_reference(monkeypatch):
    monkeypatch.setattr(config, 'LLM_PROVIDER', 'gemini')
    context = {'concept': 'Search'}
    valid = tool_arguments('visualization', context)
    broken = json.loads(valid['slideshow_json'])
    broken['steps'][0]['active_nodes'] = ['missing']
    invalid = {**valid, 'slideshow_json': json.dumps(broken)}
    model = AsyncMock(side_effect=[
        SimpleNamespace(output=[SimpleNamespace(type='function_call', name='visualization', arguments=json.dumps(args))])
        for args in (invalid, valid)
    ])
    monkeypatch.setattr(utils, 'call_llm', model)
    response = await tutor.execute_intervention({'action': 'visualization'}, context)
    assert response['metadata']['visualization']['steps'][0]['active_nodes'] == ['full']
    assert 'existing nodes' in model.call_args.args[1]['repair_error'][0]['msg']

