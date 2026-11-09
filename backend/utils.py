"""Shared validation, model clients, provider adapters, and metrics."""
import asyncio
import json
import time
from types import SimpleNamespace
from collections import Counter
from functools import lru_cache
from typing import Literal
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator
from . import config, prompts, logging_setup as logs
from .response import Schema, Response

METRICS = Counter()


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
    response: Response | None = None


class SessionData(BaseModel):
    model_config = ConfigDict(extra='allow')
    current_concept: str = Field(default='', max_length=200)
    current_problem: dict[str, JsonValue] = Field(default_factory=dict)
    plan: dict[str, JsonValue] = Field(default_factory=dict)
    observer: dict[str, JsonValue] = Field(default_factory=dict)
    revision: int = 0
    proactive_enabled: bool = True
    workspace: str = Field(default='', max_length=20000)
    response: Response | None = None
    response_revision: int = 0
    ended: bool = False


def normalize_mastery(values) -> dict[str, float]:
    """Accept saved mastery numbers, converting legacy {a, b} records and dropping unreadable ones."""
    result = {}
    for name, value in (values or {}).items() if isinstance(values, dict) else ():
        if isinstance(value, dict):
            a, b = value.get('a'), value.get('b')
            if not all(isinstance(x, (int, float)) and x >= 0 for x in (a, b)) or a + b <= 0:
                continue
            value = a / (a + b)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result[str(name)] = round(max(0.0, min(1.0, float(value))), 4)
    return result


class StudentData(BaseModel):
    model_config = ConfigDict(extra='forbid')
    mastery: dict[str, float] = Field(default_factory=dict)

    @field_validator('mastery', mode='before')
    @classmethod
    def _legacy_mastery(cls, value):
        return normalize_mastery(value)
    misconceptions: list[dict[str, JsonValue]] = Field(default_factory=list)
    memories: list[str] = Field(default_factory=list)
    learning_goals: list[str] = Field(default_factory=list)
    effective_interventions: dict[str, JsonValue] = Field(default_factory=dict)
    policy: dict[str, JsonValue] = Field(default_factory=dict)
    hypotheses: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=8)


@lru_cache
def get_client() -> AsyncOpenAI:
    if config.LLM_PROVIDER == "gemini":
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is missing.")

        return AsyncOpenAI(api_key=config.GEMINI_API_KEY, base_url="https://generativelanguage.googleapis.com/v1beta/openai/", timeout=config.API_TIMEOUT, max_retries=2,)
    if not config.OPENAI_API_KEY:
        raise RuntimeError('Configure OPENAI_API_KEY in .env.')
    return AsyncOpenAI(api_key=config.OPENAI_API_KEY, timeout=config.API_TIMEOUT, max_retries=2)




async def _call_llm(
    instructions: str,
    input_data,
    model: str | None = None,
    schema: type[BaseModel] | None = None,
    operation: str | None = None,
    max_output_tokens: int | None = None,
    **kwargs,
):
    """Support OpenAI Responses and Gemini-compatible Chat Completions."""
    started = time.perf_counter()
    METRICS["llm_calls"] += 1

    try:
        client = get_client()
        provider = config.LLM_PROVIDER

        selected_model = model or (
            config.GEMINI_MODEL
            if provider == "gemini"
            else config.OPENAI_MODEL
        )

        if not selected_model:
            raise ValueError(f"No model configured for {provider}.")

        payload = (
            input_data
            if isinstance(input_data, str)
            else json.dumps(input_data)
        )

        if len(payload) > config.MAX_INPUT_CHARS:
            payload = (
                payload[:config.MAX_INPUT_CHARS]
                + prompts.CONTEXT_TRUNCATED
            )

        timeout = config.API_TIMEOUT * 3 + 5
        wants_tools = bool(kwargs.get("tools"))
        output_budget = max_output_tokens if max_output_tokens is not None else config.MAX_OUTPUT_TOKENS

        if schema is not None and wants_tools:
            raise ValueError(
                "Request structured output and tool calls separately."
            )

        # Existing OpenAI implementation.
        if provider == "openai":
            args = dict(
                model=selected_model,
                instructions=instructions,
                input=payload,
                max_output_tokens=output_budget,
                store=False,
                **kwargs,
            )

            logs.event('llm.request', prompt=operation, provider=provider, request=args, schema=schema.model_json_schema() if schema else None)
            request = (
                client.responses.parse(**args, text_format=schema)
                if schema is not None
                else client.responses.create(**args)
            )

            response = await asyncio.wait_for(request, timeout=timeout)
            logs.event('llm.response', prompt=operation, response=response)

            if response.usage:
                METRICS["input_tokens"] += response.usage.input_tokens
                METRICS["output_tokens"] += response.usage.output_tokens

            if schema is not None:
                if response.output_parsed is None:
                    raise ValueError(
                        "The model did not return valid structured output."
                    )
                return response.output_parsed

            return response if wants_tools else response.output_text

        # Gemini's OpenAI-compatible Chat Completions implementation.
        extra = dict(kwargs)
        raw_tools = extra.pop("tools", None)
        tool_choice = extra.pop("tool_choice", None)

        # Exact tool-call count is validated by the Tutor.
        extra.pop("parallel_tool_calls", None)

        args = dict(
            model=selected_model,
            messages=[
                {"role": "system", "content": instructions},
                {"role": "user", "content": payload},
            ],
            max_tokens=output_budget,
            **extra,
        )

        if raw_tools:
            converted_tools = []

            for tool in raw_tools:
                if tool.get("type") != "function":
                    raise ValueError(
                        "This adapter supports function tools only."
                    )

                # Accept flat Responses definitions or nested Chat definitions.
                function = tool.get("function", tool)

                converted_tools.append({
                    "type": "function",
                    "function": {
                        key: function[key]
                        for key in ("name", "description", "parameters")
                        if key in function
                    },
                })

            args["tools"] = converted_tools

            if tool_choice is not None:
                if (
                    isinstance(tool_choice, dict)
                    and tool_choice.get("type") == "function"
                    and "name" in tool_choice
                ):
                    tool_choice = {
                        "type": "function",
                        "function": {"name": tool_choice["name"]},
                    }

                args["tool_choice"] = tool_choice

        logs.event('llm.request', prompt=operation, provider=provider, request=args, schema=schema.model_json_schema() if schema else None)
        request = (
            client.chat.completions.parse(
                **args,
                response_format=schema,
            )
            if schema is not None
            else client.chat.completions.create(**args)
        )

        response = await asyncio.wait_for(request, timeout=timeout)
        logs.event('llm.response', prompt=operation, response=response)

        if response.usage:
            METRICS["input_tokens"] += (
                response.usage.prompt_tokens or 0
            )
            METRICS["output_tokens"] += (
                response.usage.completion_tokens or 0
            )

        if not response.choices:
            raise ValueError("Gemini returned no response choices.")

        choice = response.choices[0]
        message = choice.message

        if choice.finish_reason == "length":
            raise ValueError(
                "Gemini reached the output token limit. "
                "Increase MAX_OUTPUT_TOKENS."
            )

        if schema is not None:
            if message.parsed is None:
                raise ValueError(
                    "Gemini did not return valid structured output."
                )
            return message.parsed

        if wants_tools:
            calls = [
                SimpleNamespace(
                    type="function_call",
                    name=call.function.name,
                    arguments=call.function.arguments,
                )
                for call in (message.tool_calls or [])
                if call.type == "function"
            ]

            if not calls:
                raise ValueError(
                    "Gemini did not return the requested function call."
                )

            return SimpleNamespace(output=calls)

        if not message.content:
            raise ValueError("Gemini returned no text content.")

        return message.content

    except Exception as exc:
        METRICS["llm_errors"] += 1
        logs.event('llm.failed', prompt=operation, error=type(exc).__name__)
        raise

    finally:
        logs.event('llm.complete', prompt=operation, duration_ms=round((time.perf_counter()-started)*1000, 2))
        METRICS["api_latency_ms"] += round(
            (time.perf_counter() - started) * 1000,
            2,
        )



def now() -> float:
    return time.time()


# Draft and foreground calls share the same provider budget, including tool calls.
_PROVIDER_LIMITS = {}


async def call_llm(*args, **kwargs):
    loop = asyncio.get_running_loop()
    limiter = _PROVIDER_LIMITS.setdefault(loop, asyncio.Semaphore(config.PROVIDER_CONCURRENCY))
    async with limiter:
        return await _call_llm(*args, **kwargs)
