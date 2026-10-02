"""Strict dotenv-style parser for .env.local.

Strict on purpose (fail fast): no shell interpolation, no `export`, no line
continuation. Values are taken literally except for one pair of surrounding
matching quotes. Malformed lines raise EnvFileError with the line number.
"""

from __future__ import annotations

import re
from pathlib import Path

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvFileError(Exception):
    """Raised when .env.local cannot be parsed or is missing."""


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv file from disk. Missing file raises (caller decides)."""
    if not path.is_file():
        raise EnvFileError(f"env file not found: {path}")
    return parse_env_text(path.read_text(encoding="utf-8"))


def parse_env_text(text: str) -> dict[str, str]:
    """Parse dotenv text into an ordered mapping. Later assignment wins."""

    def strip_quotes(value: str) -> str:
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            return value[1:-1]
        return value

    result: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise EnvFileError(f"env file line {lineno}: expected KEY=VALUE, got: {line!r}")
        key, _, value = line.partition("=")
        key = key.strip()
        if not _KEY_RE.match(key):
            raise EnvFileError(f"env file line {lineno}: invalid key name {key!r}")
        result[key] = strip_quotes(value.strip())
    return result
