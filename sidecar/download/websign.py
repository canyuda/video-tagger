"""x-secsdk-web-signature — port of lib/websign.js.

The JS mimicked urllib.parse semantics; back in Python we use urllib.parse
itself, which is the original reference behavior (unquote / quote with safe).
"""

from __future__ import annotations

import hashlib
import time
from urllib.parse import quote, unquote

SALT = "A96D855A08C0A9707F8BEF0D9A527E4E"
SIGNATURE_PARAM = "x-secsdk-web-signature"
UIFID_PARAM = "uifid"
TIMESTAMP_PARAM = "timestamp"


def _split_pairs_keep_plus(query: str) -> list[tuple[str, str]]:
    """Decode query pairs with JS unquote semantics: %XX decoded, literal
    '+' preserved (urllib.parse.unquote, not unquote_plus)."""
    pairs = []
    for part in query.split("&"):
        if not part:
            continue
        if "=" in part:
            name, value = part.split("=", 1)
        else:
            name, value = part, ""
        pairs.append((unquote(name), unquote(value)))
    return pairs


def _encode_pairs(pairs: list[tuple[str, str]]) -> str:
    return "&".join(
        f"{quote(name, safe='*-._')}={quote(value, safe='*-._')}" for name, value in pairs
    )


def normalize_query(query: str) -> str:
    """Canonical query byte sequence emitted by WebSign."""
    return _encode_pairs(_split_pairs_keep_plus(query))


def sign(query: str, uifid: str, timestamp: int | None = None) -> tuple[str, str]:
    """Append visitor timestamp and compute x-secsdk-web-signature.
    Returns (signed_query, signature); query must already carry the uifid."""
    stamp = str(timestamp if timestamp is not None else int(time.time()))
    pairs = _split_pairs_keep_plus(query)
    if not any(name == UIFID_PARAM for name, _ in pairs):
        pairs.append((UIFID_PARAM, uifid))
    pairs.append((TIMESTAMP_PARAM, stamp))
    hashed = _encode_pairs(pairs)
    signature = hashlib.md5(
        f"{uifid}_{stamp}_{SALT}_{hashed}".encode()
    ).hexdigest()
    return f"{hashed}&{SIGNATURE_PARAM}={signature}", signature
