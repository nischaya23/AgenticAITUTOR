"""Shared validation and bounded, metered OpenAI Responses calls."""
import asyncio
import json
import time
from collections import Counter
from functools import lru_cache
from typing import Literal
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from . import config

METRICS = Counter()


class Schema(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ClientEvent(Schema):
    type: Literal['student_message', 'student_action', 'problem_attempt',
                  'code_execution', 'feedback', 'session_end', 'preferences']
    content: str = Field(default='', max_length=20000)
    action: Literal['edit', 'revert', 'problem_opened', 'idle', 'hint_request', ''] = ''
    tool: Literal['hint', 'explanation', 'highlight', 'visualization', 'guiding_question'] | None = None
    rating: Literal[-1, 1] | None = None
    intervention_id: int | None = None
    proactive_enabled: bool | None = None
    problem_id: str | None = Field(default=None, max_length=100)
    assessment_mode: Literal['answer', 'reasoning'] = 'answer'


class SessionData(BaseModel):
    model_config = ConfigDict(extra='allow')
    current_concept: str = Field(default='Binary Search', max_length=200)
    current_problem: dict[str, JsonValue] = Field(default_factory=dict)
    plan: dict[str, JsonValue] = Field(default_factory=dict)
    observer: dict[str, JsonValue] = Field(default_factory=dict)
    revision: int = 0
    proactive_enabled: bool = True
    workspace: str = Field(default='', max_length=20000)
    ended: bool = False


class StudentData(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mastery: dict[str, float] = Field(default_factory=dict)
    misconceptions: list[dict[str, JsonValue]] = Field(default_factory=list)
    memories: list[str] = Field(default_factory=list)
    learning_goals: list[str] = Field(default_factory=list)
    effective_interventions: dict[str, JsonValue] = Field(default_factory=dict)
    policy: dict[str, JsonValue] = Field(default_factory=dict)


@lru_cache
def get_client() -> AsyncOpenAI:
    if not config.OPENAI_API_KEY:
        raise RuntimeError('Add OPENAI_API_KEY to .env, or set DEMO_MODE=true for the offline demo.')
    return AsyncOpenAI(api_key=config.OPENAI_API_KEY, timeout=config.API_TIMEOUT, max_retries=2)


async def call_llm(instructions: str, input_data, model: str | None = None,
                   schema: type[BaseModel] | None = None, **kwargs):
    """SDK retries transient errors twice; wall timeout includes all retries."""
    client = get_client()
    started = time.perf_counter()
    METRICS['llm_calls'] += 1
    payload = input_data if isinstance(input_data, str) else json.dumps(input_data)
    if len(payload) > config.MAX_INPUT_CHARS:
        payload = payload[:config.MAX_INPUT_CHARS] + '\n[Context truncated to input budget.]'
    args = dict(model=model or config.OPENAI_MODEL, instructions=instructions,
                input=payload,
                max_output_tokens=config.MAX_OUTPUT_TOKENS, store=False, **kwargs)
    try:
        async with asyncio.timeout(config.API_TIMEOUT * 3 + 5):
            response = await (client.responses.parse(**args, text_format=schema)
                              if schema else client.responses.create(**args))
        if response.usage:
            METRICS['input_tokens'] += response.usage.input_tokens
            METRICS['output_tokens'] += response.usage.output_tokens
        if schema:
            if response.output_parsed is None:
                raise ValueError('The model did not return a valid structured answer.')
            return response.output_parsed
        return response if 'tools' in kwargs else response.output_text
    except Exception:
        METRICS['llm_errors'] += 1
        raise
    finally:
        METRICS['api_latency_ms'] += round((time.perf_counter() - started) * 1000, 2)


def now() -> float:
    return time.time()
