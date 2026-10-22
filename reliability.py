"""Small dependency-free reliability primitives for external providers."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

logger = logging.getLogger("tripmate.reliability")


class CircuitOpenError(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 3, recovery_seconds: int = 30):
        self.name = name
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_seconds = max(1, recovery_seconds)
        self.failures = 0
        self.opened_at = 0.0

    def before_call(self) -> None:
        if not self.opened_at:
            return
        if time.monotonic() - self.opened_at < self.recovery_seconds:
            raise CircuitOpenError(f"Provider circuit is open: {self.name}")
        self.opened_at = 0.0

    def success(self) -> None:
        self.failures = 0
        self.opened_at = 0.0

    def failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()
            logger.warning("provider circuit opened name=%s", self.name)


def call_with_retry(fn: Callable, *, breaker: CircuitBreaker, attempts: int = 3, backoff: float = 0.5):
    last_error = None
    for attempt in range(max(1, attempts)):
        breaker.before_call()
        try:
            value = fn()
            breaker.success()
            return value
        except Exception as exc:
            last_error = exc
            breaker.failure()
            if attempt + 1 < attempts:
                time.sleep(backoff * (2**attempt))
    raise last_error


async def async_call_with_retry(fn: Callable, *, breaker: CircuitBreaker, attempts: int = 3, backoff: float = 0.5):
    last_error = None
    for attempt in range(max(1, attempts)):
        breaker.before_call()
        try:
            value = await fn()
            breaker.success()
            return value
        except Exception as exc:
            last_error = exc
            breaker.failure()
            if attempt + 1 < attempts:
                await asyncio.sleep(backoff * (2**attempt))
    raise last_error
