"""Opt-in smoke test: performs one paid, structured Responses API call."""
import os
import pytest
from backend import config, tutor, utils


class Check(utils.Schema):
    answer: int


@pytest.mark.integration
@pytest.mark.skipif(os.getenv('RUN_OPENAI_INTEGRATION')!='1',reason='Opt-in paid API test')
async def test_live_responses_api(monkeypatch):
    assert config.OPENAI_API_KEY, 'Provide OPENAI_API_KEY to run the paid smoke test.'
    monkeypatch.setattr(config, 'LLM_PROVIDER', 'openai')
    result=await utils.call_llm('Answer the arithmetic question.','What is 2 + 2?',schema=Check)
    assert result.answer==4


@pytest.mark.integration
@pytest.mark.skipif(os.getenv('RUN_OPENAI_INTEGRATION')!='1', reason='Opt-in paid API test')
@pytest.mark.parametrize('kind', ['highlight', 'visualization'])
async def test_live_teaching_function(monkeypatch, kind):
    assert config.OPENAI_API_KEY, 'Provide OPENAI_API_KEY to run the paid smoke test.'
    monkeypatch.setattr(config, 'LLM_PROVIDER', 'openai')
    monkeypatch.setattr(config, 'DEMO_MODE', False)
    context = dict(concept='Binary Search', problem={'prompt': 'Find the first 2 in [1, 2, 2, 5].'},
                   workspace='If the midpoint equals 2, set left = mid + 1.',
                   knowledge=[], query='Review the update and illustrate the lower-bound binary search.')
    result = await tutor.execute_intervention({'action': kind, 'strategy': 'Show the interval invariant.'}, context)
    assert result['type'] == kind
    assert ('annotations' if kind == 'highlight' else 'visualization') in result['metadata']
