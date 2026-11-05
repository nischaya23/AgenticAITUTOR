"""Opt-in smoke test: performs one paid, structured Responses API call."""
import os
import pytest
from backend import config, utils


class Check(utils.Schema):
    answer: int


@pytest.mark.integration
@pytest.mark.skipif(os.getenv('RUN_OPENAI_INTEGRATION')!='1',reason='Opt-in paid API test')
async def test_live_responses_api():
    assert config.OPENAI_API_KEY, 'Provide OPENAI_API_KEY to run the paid smoke test.'
    result=await utils.call_llm('Answer the arithmetic question.','What is 2 + 2?',schema=Check)
    assert result.answer==4
