import asyncio
import httpx
import pytest
from openai import APITimeoutError, BadRequestError, InternalServerError, RateLimitError
from backend import config, utils
from conftest import REAL_CALL_LLM

REQUEST = httpx.Request('POST', 'https://example.test/completions')


def error(cls, code, headers=None):
    return cls('overloaded', response=httpx.Response(code, request=REQUEST, headers=headers or {}), body={})


@pytest.fixture
def fast_retries(monkeypatch):
    monkeypatch.setattr(config, 'LLM_MAX_ATTEMPTS', 4)
    monkeypatch.setattr(config, 'LLM_BACKOFF_SECONDS', 0)
    monkeypatch.setattr(config, 'LLM_MAX_BACKOFF_SECONDS', 0)
    monkeypatch.setattr(config, 'LLM_PROVIDER', 'gemini')
    monkeypatch.setattr(config, 'GEMINI_FALLBACK_MODEL', '')
    utils._PROVIDER_COOLDOWN.clear()
    yield
    utils._PROVIDER_COOLDOWN.clear()


async def test_overloaded_provider_is_retried_until_it_recovers(fast_retries, monkeypatch):
    calls = []
    async def flaky(*args, **kwargs):
        calls.append(kwargs.get('model'))
        if len(calls) < 3:
            raise error(InternalServerError, 503)
        return 'ok'
    monkeypatch.setattr(utils, '_call_llm', flaky)
    assert await REAL_CALL_LLM('instructions', {}, operation='EXTRACT_CONCEPTS') == 'ok'
    assert len(calls) == 3


async def test_rate_limit_retries_then_raises_after_last_attempt(fast_retries, monkeypatch):
    calls = 0
    async def limited(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise error(RateLimitError, 429)
    monkeypatch.setattr(utils, '_call_llm', limited)
    with pytest.raises(RateLimitError):
        await REAL_CALL_LLM('instructions', {})
    assert calls == config.LLM_MAX_ATTEMPTS


@pytest.mark.parametrize('exc', [error(BadRequestError, 400), APITimeoutError(request=REQUEST), ValueError('bad output')])
async def test_non_transient_failures_are_not_retried(fast_retries, monkeypatch, exc):
    calls = 0
    async def broken(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise exc
    monkeypatch.setattr(utils, '_call_llm', broken)
    with pytest.raises(type(exc)):
        await REAL_CALL_LLM('instructions', {})
    assert calls == 1


async def test_fallback_model_is_used_for_later_attempts_only(fast_retries, monkeypatch):
    monkeypatch.setattr(config, 'GEMINI_FALLBACK_MODEL', 'backup-model')
    models = []
    async def overloaded(*args, **kwargs):
        models.append(kwargs.get('model'))
        if len(models) < 4:
            raise error(InternalServerError, 503)
        return 'ok'
    monkeypatch.setattr(utils, '_call_llm', overloaded)
    assert await REAL_CALL_LLM('instructions', {}) == 'ok'
    assert models == [None, None, 'backup-model', 'backup-model']


async def test_explicit_model_is_never_replaced(fast_retries, monkeypatch):
    monkeypatch.setattr(config, 'GEMINI_FALLBACK_MODEL', 'backup-model')
    models = []
    async def overloaded(*args, **kwargs):
        models.append(kwargs.get('model'))
        if len(models) < 4:
            raise error(InternalServerError, 503)
        return 'ok'
    monkeypatch.setattr(utils, '_call_llm', overloaded)
    await REAL_CALL_LLM('instructions', {}, model='chosen')
    assert set(models) == {'chosen'}


async def test_overload_pauses_other_callers_and_frees_the_slot(fast_retries, monkeypatch):
    monkeypatch.setattr(config, 'LLM_BACKOFF_SECONDS', .05)
    monkeypatch.setattr(config, 'LLM_MAX_BACKOFF_SECONDS', .05)
    monkeypatch.setattr(config, 'PROVIDER_CONCURRENCY', 1)
    utils._PROVIDER_LIMITS.pop(asyncio.get_running_loop(), None)
    first = True
    async def call(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            raise error(InternalServerError, 503)
        return 'ok'
    monkeypatch.setattr(utils, '_call_llm', call)
    results = await asyncio.gather(*(REAL_CALL_LLM('instructions', {}) for _ in range(3)))
    assert results == ['ok'] * 3
    assert utils._PROVIDER_COOLDOWN  # A shared cooldown was set by the overload.
    utils._PROVIDER_LIMITS.pop(asyncio.get_running_loop(), None)


def test_backoff_grows_is_capped_and_honours_retry_after(monkeypatch):
    monkeypatch.setattr(config, 'LLM_BACKOFF_SECONDS', 2)
    monkeypatch.setattr(config, 'LLM_MAX_BACKOFF_SECONDS', 30)
    assert 1 <= utils.backoff_delay(1) <= 2
    assert 4 <= utils.backoff_delay(3) <= 8
    assert utils.backoff_delay(10) <= 30
    assert utils.backoff_delay(1, error(RateLimitError, 429, {'retry-after': '12'})) >= 12
