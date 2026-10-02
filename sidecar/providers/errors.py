"""Provider error taxonomy: retryable vs terminal, with code prefixes (docs/04 §7)."""

from __future__ import annotations

# HTTP statuses we retry automatically: rate limits, provider overload, 5xx.
RETRYABLE_STATUSES = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


class ProviderError(Exception):
    """One error for all providers. `retryable` drives queue-level retry;
    `code` prefixes the HTTP status or failure kind (e.g. CHAT-429, DEC-529,
    CHAT-TIMEOUT). Messages must never contain API keys or full payloads."""

    def __init__(self, code: str, message: str, retryable: bool) -> None:
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.retryable = retryable

    @classmethod
    def from_status(cls, prefix: str, status_code: int, detail: str) -> ProviderError:
        retryable = status_code in RETRYABLE_STATUSES
        return cls(f"{prefix}-{status_code}", f"HTTP {status_code}: {detail[:300]}", retryable)

    @classmethod
    def network(cls, prefix: str, cause: str) -> ProviderError:
        return cls(f"{prefix}-NETWORK", cause[:300], retryable=True)

    @classmethod
    def terminal(cls, prefix: str, kind: str, detail: str) -> ProviderError:
        return cls(f"{prefix}-{kind}", detail[:300], retryable=False)
