"""VK API rate-limit constants and helpers.

VK community tokens get ~20 req/sec on messaging methods (per official docs);
``messages.send`` is additionally subject to anti-spam throttling per-recipient.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Awaitable, Callable, TypeVar

logger = logging.getLogger(__name__)

VK_MESSAGE_LENGTH = 4096       # messages.send hard limit
VK_ATTACHMENTS_PER_MESSAGE = 10

RETRY_MAX_ATTEMPTS = 4
RETRY_BACKOFF_BASE = 1.5
RETRY_BACKOFF_CAP = 30.0


T = TypeVar("T")


def is_vk_rate_limit(exc: BaseException) -> bool:
    """Detect VK API throttle errors (codes 6, 9 — too many requests / flood)."""
    code = getattr(exc, "code", None) or getattr(exc, "error_code", None)
    if code in {6, 9}:
        return True
    text = str(exc).lower()
    return "too many" in text or "flood" in text


def retry_after(_exc: BaseException) -> float | None:
    # VK doesn't expose Retry-After; rely on backoff.
    return None


async def with_backoff(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = RETRY_MAX_ATTEMPTS,
) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return await fn()
        except BaseException as exc:  # noqa: BLE001
            if not is_vk_rate_limit(exc) or attempt >= max_attempts:
                raise
            wait = min(RETRY_BACKOFF_CAP, RETRY_BACKOFF_BASE ** attempt) + random.uniform(0, 0.5)
            logger.warning("VK rate limit on attempt %d/%d; sleeping %.1fs", attempt, max_attempts, wait)
            await asyncio.sleep(wait)
