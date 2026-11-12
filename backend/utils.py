"""Shared validation, model clients, provider adapters, and metrics."""
import asyncio
import json
import random
import time
from types import SimpleNamespace
from collections import Counter
from functools import lru_cache
from typing import Literal
from openai import AsyncOpenAI, DefaultAsyncHttpxClient
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from . import workflow, config, prompts, logging_setup as logs
from .response import Schema, Response

METRICS = Counter()


class ClientEvent(Schema):
    type: Literal['student_message', 'student_action', 'problem_attempt',
                  'code_execution', 'feedback', 'session_end', 'preferences']
    content: str = Field(default='', max_length=20000)
    action: Literal['edit', 'revert', 'problem_opened', 'idle', 'hint_request', ''] = ''
    tool: Literal['hint', 'explanation', 'highlight', 'visualization', 'guiding_question', 'web_search'] | None = None
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
    misconceptions: list[dict[str, JsonValue]] = Field(default_factory=list)
    memories: list[str] = Field(default_factory=list)
    learning_goals: list[str] = Field(default_factory=list)
    effective_interventions: dict[str, JsonValue] = Field(default_factory=dict)
    policy: dict[str, JsonValue] = Field(default_factory=dict)
    hypotheses: list[dict[str, JsonValue]] = Field(default_factory=list, max_length=8)
    memory_revision: int = 0
    student_goals: list[str] = Field(default_factory=list)


async def provider_request(request):
    # SDK retry metadata is read locally; headers, URLs and credentials are never stored.
    count = request.headers.get('x-stainless-retry-count', '0')
    workflow.record('provider.attempt', status='running', attempt=int(count)+1 if count.isdigit() else 1)


async def provider_response(response):
    count = response.request.headers.get('x-stainless-retry-count', '0')
    workflow.record('provider.response', status='warnings' if response.status_code >= 400 else 'completed',
        attempt=int(count)+1 if count.isdigit() else 1, http_status=response.status_code,
        retryable=response.status_code in (408, 409, 429) or response.status_code >= 500)


def provider_transport():
    return DefaultAsyncHttpxClient(event_hooks={'request': [provider_request], 'response': [provider_response]})


@lru_cache
def get_client() -> AsyncOpenAI:
    if config.LLM_PROVIDER == "gemini":
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is missing.")

        # One quick SDK retry; longer backoff rounds are handled by call_llm.
        return AsyncOpenAI(api_key=config.GEMINI_API_KEY, base_url="https://generativelanguage.googleapis.com/v1beta/openai/", timeout=config.API_TIMEOUT, max_retries=1, http_client=provider_transport())
    if not config.OPENAI_API_KEY:
        raise RuntimeError('Configure OPENAI_API_KEY in .env.')
    return AsyncOpenAI(api_key=config.OPENAI_API_KEY, timeout=config.API_TIMEOUT, max_retries=1, http_client=provider_transport())




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

        from .context import compact_payload
        input_data = compact_payload(input_data)
        payload = (
            input_data
            if isinstance(input_data, str)
            else json.dumps(input_data)
        )

        if len(payload) > config.MAX_INPUT_CHARS:
            raise ValueError("Context exceeds the input budget; shorten the current task.")

        timeout = config.API_TIMEOUT * 3 + 5
        history = kwargs.pop("tool_history", [])
        workflow.model_context(input_data, history)
        wants_tools = bool(kwargs.get("tools"))
        output_budget = max_output_tokens if max_output_tokens is not None else config.MAX_OUTPUT_TOKENS

        if schema is not None and wants_tools:
            raise ValueError(
                "Request structured output and tool calls separately."
            )

        native_input = [{'role': 'user', 'content': payload}]
        chat_history = []
        for turn in history:
            native_input.extend([
                {'type': 'function_call', 'call_id': turn['call_id'], 'name': turn['name'], 'arguments': turn['arguments']},
                {'type': 'function_call_output', 'call_id': turn['call_id'], 'output': json.dumps(turn['result'])}])
            chat_history.extend([
                {'role': 'assistant', 'tool_calls': [turn.get('provider_call') or {'id': turn['call_id'], 'type': 'function',
                    'function': {'name': turn['name'], 'arguments': turn['arguments']}}]},
                {'role': 'tool', 'tool_call_id': turn['call_id'], 'content': json.dumps(turn['result'])}])

        # Existing OpenAI implementation.
        if provider == "openai":
            args = dict(
                model=selected_model,
                instructions=instructions,
                input=native_input if history else payload,
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
                workflow.record('model.usage', 'agents', input_tokens=response.usage.input_tokens, output_tokens=response.usage.output_tokens)
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
                *chat_history,
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
            workflow.record('model.usage', 'agents', input_tokens=response.usage.prompt_tokens, output_tokens=response.usage.completion_tokens)
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
                    call_id=call.id,
                    provider_call=call.model_dump(exclude_none=True) if hasattr(call,'model_dump') else dict(
                        id=call.id,type='function',function=dict(name=call.function.name,arguments=call.function.arguments),
                        **({'extra_content':call.extra_content} if getattr(call,'extra_content',None) else {})),
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


# Monotonic time until which new provider calls wait after an overload response (per event loop).
_PROVIDER_COOLDOWN = {}


def is_retryable(exc: BaseException) -> bool:
    """Transient provider failures: overload/rate limit (429, 5xx), conflicts and dropped connections.

    Timeouts are not retried here: each one already waited the full request budget.
    """
    from openai import APIConnectionError, APIStatusError, APITimeoutError
    if isinstance(exc, APITimeoutError):
        return False
    if isinstance(exc, APIConnectionError):
        return True
    return isinstance(exc, APIStatusError) and (exc.status_code in (408, 409, 429) or exc.status_code >= 500)


def backoff_delay(attempt: int, exc: BaseException | None = None) -> float:
    """Exponential backoff with jitter, honouring a provider Retry-After header when present."""
    delay = min(config.LLM_MAX_BACKOFF_SECONDS, config.LLM_BACKOFF_SECONDS * 2 ** (attempt - 1))
    delay *= random.uniform(.5, 1)  # Jitter so parallel callers do not retry in lockstep.
    response = getattr(exc, 'response', None)
    hint = response.headers.get('retry-after') if response is not None else None
    try:
        delay = max(delay, min(float(hint), config.LLM_MAX_BACKOFF_SECONDS)) if hint else delay
    except ValueError:
        pass
    return delay


def _model_for(attempt: int, requested: str | None) -> str | None:
    """Keep the caller's model; otherwise switch to the configured backup model for later attempts."""
    if requested:
        return requested
    fallback = config.GEMINI_FALLBACK_MODEL if config.LLM_PROVIDER == 'gemini' else config.OPENAI_FALLBACK_MODEL
    return fallback if fallback and attempt > max(1, config.LLM_MAX_ATTEMPTS // 2) else None


async def _wait_for_cooldown(loop):
    remaining = _PROVIDER_COOLDOWN.get(loop, 0) - time.monotonic()
    if remaining > 0:
        workflow.record('provider.cooldown', status='warnings', wait_ms=round(remaining*1000))
        await asyncio.sleep(remaining)


async def call_llm(*args, **kwargs):
    loop = asyncio.get_running_loop()
    limiter = _PROVIDER_LIMITS.setdefault(loop, asyncio.Semaphore(config.PROVIDER_CONCURRENCY))
    requested_model = kwargs.pop('model', None)
    attempts = max(1, config.LLM_MAX_ATTEMPTS)
    for attempt in range(1, attempts + 1):
        model = _model_for(attempt, requested_model)
        await _wait_for_cooldown(loop)
        with workflow.span('Provider slot', 'agents', status_label='Waiting for model'):
            queued_at = time.perf_counter()
            await limiter.acquire()
            workflow.record('model.slot_acquired', queue_ms=round((time.perf_counter()-queued_at)*1000, 2))
        try:
            with workflow.span(kwargs.get('operation') or 'Model request', 'agents',
                    provider=config.LLM_PROVIDER, model=model or (config.GEMINI_MODEL if config.LLM_PROVIDER == 'gemini' else config.OPENAI_MODEL)):
                return await _call_llm(*args, **({**kwargs, 'model': model} if model else kwargs))
        except Exception as exc:
            if attempt == attempts or not is_retryable(exc):
                raise
            delay = backoff_delay(attempt, exc)
            # Overload is shared: make every caller pause, not only this one.
            _PROVIDER_COOLDOWN[loop] = max(_PROVIDER_COOLDOWN.get(loop, 0), time.monotonic() + delay)
            workflow.record('provider.backoff', status='warnings', attempt=attempt, next_attempt=attempt + 1,
                http_status=getattr(exc, 'status_code', None), wait_ms=round(delay*1000))
            logs.event('llm.retry', prompt=kwargs.get('operation'), attempt=attempt, error=type(exc).__name__,
                       wait_ms=round(delay*1000))
        finally:
            # The slot is released before waiting so other work is not blocked by this backoff.
            limiter.release()
