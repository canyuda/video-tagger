"""Retry policy for external APIs (D53): exponential backoff + jitter, at most
3 attempts, then surface as retryable error. Sleep is injectable for tests."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass

from sidecar.providers.errors import ProviderError

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 8.0
JITTER_FRACTION = 0.25


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_delay: float = DEFAULT_BASE_DELAY
    max_delay: float = DEFAULT_MAX_DELAY

    def backoff_delay(self, attempt: int, rng: random.Random) -> float:
        """Delay before retrying after failed `attempt` (0-based), with jitter."""
        exponential = min(self.max_delay, self.base_delay * (2 ** attempt))
        jitter = 1.0 + rng.random() * JITTER_FRACTION
        return exponential * jitter


def with_retries(
    fn: Callable[[], object],
    policy: RetryPolicy | None = None,
    sleep: Callable[[float], None] = lambda s: None,
    rng: random.Random | None = None,
) -> object:
    """Run `fn` under the retry policy. ProviderErrors marked terminal
    propagate immediately; retryable ones are retried up to policy attempts.
    The final error re-raises with its retryable flag intact (the queue layer
    decides what a retryable failure means for the task)."""
    p = policy or RetryPolicy()
    r = rng or random.Random()
    last_error: ProviderError | None = None
    for attempt in range(p.max_attempts):
        try:
            return fn()
        except ProviderError as e:
            if not e.retryable:
                raise
            last_error = e
            if attempt < p.max_attempts - 1:
                sleep(p.backoff_delay(attempt, r))
    assert last_error is not None
    raise last_error
