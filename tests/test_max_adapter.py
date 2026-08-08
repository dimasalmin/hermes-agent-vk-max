"""Smoke tests for MAX adapter (rate-limit helpers and pure utilities).

Full adapter integration tests live in Hermes's own test suite (run via
``scripts/run_tests.sh`` once the plugin is dropped into ``~/.hermes/plugins/``).
This file exercises the parts that don't require importing Hermes core.
"""

from __future__ import annotations

import asyncio

import pytest

from plugins.max.rate_limit import (
    MAX_MESSAGE_LENGTH,
    RETRY_BACKOFF_BASE,
    with_backoff,
)


def test_max_message_length_matches_documented_limit():
    assert MAX_MESSAGE_LENGTH == 4000


def test_retry_backoff_base_is_above_one():
    assert RETRY_BACKOFF_BASE > 1.0


class _Throttled(Exception):
    status_code = 429


def _always_rl(_exc: BaseException) -> bool:
    return True


def _never_rl(_exc: BaseException) -> bool:
    return False


def _retry_after_zero(_exc: BaseException) -> float:
    return 0.0


@pytest.mark.asyncio
async def test_with_backoff_retries_until_success():
    calls = {"n": 0}

    async def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _Throttled("nope")
        return "ok"

    result = await with_backoff(
        fn, is_rate_limit=_always_rl, extract_retry_after=_retry_after_zero, max_attempts=5
    )
    assert result == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_with_backoff_exhausts_attempts():
    async def fn():
        raise _Throttled("nope")

    with pytest.raises(_Throttled):
        await with_backoff(
            fn, is_rate_limit=_always_rl, extract_retry_after=_retry_after_zero, max_attempts=2
        )


@pytest.mark.asyncio
async def test_with_backoff_does_not_retry_non_rate_limit():
    async def fn():
        raise ValueError("nope")

    with pytest.raises(ValueError):
        await with_backoff(
            fn, is_rate_limit=_never_rl, extract_retry_after=lambda _e: None, max_attempts=5
        )
