"""Taxonomy: configurable classification types (D09/D43, docs/03 §3.0).

`name_en` is the stable key everywhere (Jev options, SQLite/CSV, sample store);
Chinese `name` is UI display only. Custom types may leave name_en blank — the
effective key then falls back to the Chinese name (documented precision
tradeoff). The builtin 11-type template ships as packaged JSON data.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

BUILTIN_TAXONOMY_PATH = Path(__file__).parent / "default.json"


class TaxonomyError(Exception):
    """Raised when taxonomy data is missing, malformed, or inconsistent."""


class TaxonomyType(BaseModel):
    name: str
    name_en: str = ""
    worthy: bool
    hint: str
    hint_en: str | None = None


class Taxonomy(BaseModel):
    version: int
    types: list[TaxonomyType]

    def effective_keys(self) -> list[str]:
        return [effective_key(t) for t in self.types]

    def choice_criteria(self) -> dict[str, str]:
        """Option name -> description for the Jev Choice question (English
        hint_en preferred; Chinese hint fallback per D43)."""
        return {effective_key(t): (t.hint_en or t.hint) for t in self.types}


def effective_key(t: TaxonomyType) -> str:
    """Stable storage/decision key: name_en, falling back to Chinese name."""
    return t.name_en.strip() or t.name.strip()


def load_taxonomy(source: dict | str) -> Taxonomy:
    """Parse and validate taxonomy from a dict or JSON text. Fail fast with
    a specific message — never silently drop types."""
    data = _parse(source)
    try:
        taxonomy = Taxonomy.model_validate(data)
    except Exception as e:  # noqa: BLE001 — wrap into one domain error type
        raise TaxonomyError(f"invalid taxonomy structure: {e}") from e

    if taxonomy.version < 1:
        raise TaxonomyError(f"taxonomy version must be >= 1, got {taxonomy.version}")
    if not taxonomy.types:
        raise TaxonomyError("taxonomy must contain at least one type")

    keys = taxonomy.effective_keys()
    if any(not k for k in keys):
        raise TaxonomyError("every type needs a name or name_en")
    dupes = sorted({k for k in keys if keys.count(k) > 1})
    if dupes:
        raise TaxonomyError(f"duplicate type keys: {', '.join(dupes)}")
    if "other" not in keys:
        raise TaxonomyError("taxonomy must include an 'other' fallback type (docs/03 §3.0)")
    return taxonomy


def builtin_taxonomy() -> Taxonomy:
    """Load the packaged default 11-type taxonomy (v1)."""
    try:
        return load_taxonomy(BUILTIN_TAXONOMY_PATH.read_text(encoding="utf-8"))
    except OSError as e:
        raise TaxonomyError(f"cannot read builtin taxonomy: {e}") from e


def worthy_of(taxonomy: Taxonomy, key: str) -> bool:
    """Value mapping lives in config, not in the model (D09): changing worthy
    flags never re-runs classification."""
    for t in taxonomy.types:
        if effective_key(t) == key:
            return t.worthy
    raise KeyError(f"unknown type key: {key!r}")


def _parse(source: dict | str) -> dict:
    if isinstance(source, dict):
        return source
    if isinstance(source, str):
        try:
            data = json.loads(source)
        except json.JSONDecodeError as e:
            raise TaxonomyError(f"taxonomy is not valid JSON: {e}") from e
        if isinstance(data, dict):
            return data
    raise TaxonomyError("taxonomy must be a JSON object or equivalent dict")
