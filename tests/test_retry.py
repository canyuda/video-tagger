"""Tests for retry policy: backoff shape, terminal short-circuit, max attempts."""

import random

from sidecar.providers.errors import ProviderError
from sidecar.providers.retry import RetryPolicy, with_retries

RETRYABLE = ProviderError("CHAT-429", "rate limited", retryable=True)
TERMINAL = ProviderError("CHAT-401", "bad key", retryable=False)


class TestBackoffDelay:
    def test_exponential_growth(self):
        p = RetryPolicy()
        rng = random.Random(42)
        d0 = p.backoff_delay(0, rng)
        d1 = p.backoff_delay(1, rng)
        d2 = p.backoff_delay(2, rng)
        # jitter is multiplicative 1.0x-1.25x, so bounds are strict.
        assert 1.0 <= d0 <= 1.25
        assert 2.0 <= d1 <= 2.5
        assert 4.0 <= d2 <= 5.0

    def test_capped_at_max_delay(self):
        p = RetryPolicy(base_delay=100.0, max_delay=8.0)
        d = p.backoff_delay(5, random.Random(1))
        assert d <= 8.0 * 1.25

    def test_jitter_varies_between_calls(self):
        p = RetryPolicy()
        rng = random.Random(7)
        delays = {round(p.backoff_delay(0, rng), 6) for _ in range(20)}
        assert len(delays) > 1


class TestWithRetries:
    def test_success_first_attempt(self):
        calls = []
        result = with_retries(lambda: calls.append(1) or "ok")
        assert result == "ok" and len(calls) == 1

    def test_retryable_error_retried_to_success(self):
        state = {"n": 0}

        def flaky():
            state["n"] += 1
            if state["n"] < 3:
                raise RETRYABLE
            return "recovered"

        sleeps = []
        result = with_retries(flaky, sleep=sleeps.append)
        assert result == "recovered"
        assert state["n"] == 3
        assert len(sleeps) == 2  # slept between attempts only

    def test_exhausted_attempts_reraises_last(self):
        calls = []

        def always_fails():
            calls.append(1)
            raise RETRYABLE

        try:
            with_retries(always_fails, sleep=lambda _: None)
            raise AssertionError("should have raised")
        except ProviderError as e:
            assert e is RETRYABLE
        assert len(calls) == 3  # D53: at most 3 attempts

    def test_terminal_error_not_retried(self):
        calls = []

        def bad_key():
            calls.append(1)
            raise TERMINAL

        try:
            with_retries(bad_key, sleep=lambda _: None)
            raise AssertionError("should have raised")
        except ProviderError as e:
            assert e is TERMINAL
        assert len(calls) == 1

    def test_non_provider_error_propagates_uncaught(self):
        class Boom(Exception):
            pass

        def crashes():
            raise Boom("bug in our code")

        try:
            with_retries(crashes, sleep=lambda _: None)
            raise AssertionError("should have raised")
        except Boom:
            pass
