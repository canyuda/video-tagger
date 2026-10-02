"""Tests for taxonomy loading/validation and worthy mapping (docs/03 §3.0, D09/D43)."""

import json

import pytest

from sidecar.taxonomy import (
    TaxonomyError,
    builtin_taxonomy,
    effective_key,
    load_taxonomy,
    worthy_of,
)


def make_types(*entries: tuple[str, str, bool]) -> list[dict]:
    return [
        {"name": zh, "name_en": en, "worthy": w, "hint": f"hint {zh}", "hint_en": f"hint {en}"}
        for zh, en, w in entries
    ]


VALID_TYPES = make_types(
    ("口播", "monologue", True),
    ("唱歌", "singing", False),
    ("其他", "other", True),
)


class TestLoadTaxonomy:
    def test_valid_load(self):
        t = load_taxonomy({"version": 1, "types": VALID_TYPES})
        assert t.version == 1
        assert [effective_key(x) for x in t.types] == ["monologue", "singing", "other"]

    def test_missing_other_rejected(self):
        types = make_types(("口播", "monologue", True), ("唱歌", "singing", False))
        with pytest.raises(TaxonomyError, match="other"):
            load_taxonomy({"version": 1, "types": types})

    def test_empty_types_rejected(self):
        with pytest.raises(TaxonomyError):
            load_taxonomy({"version": 1, "types": []})

    def test_missing_version_rejected(self):
        with pytest.raises(TaxonomyError):
            load_taxonomy({"types": VALID_TYPES})

    def test_duplicate_effective_keys_rejected(self):
        dupe = {"name": "口播2", "name_en": "monologue", "worthy": False, "hint": "x"}
        with pytest.raises(TaxonomyError, match="monologue"):
            load_taxonomy({"version": 1, "types": VALID_TYPES + [dupe]})

    def test_blank_name_en_falls_back_to_chinese_name(self):
        # Custom type with empty name_en: key falls back to the Chinese name (D43).
        types = VALID_TYPES + [{"name": "自定义", "name_en": "", "worthy": True, "hint": "x"}]
        t = load_taxonomy({"version": 2, "types": types})
        assert effective_key(t.types[-1]) == "自定义"

    def test_blank_name_and_name_en_rejected(self):
        types = VALID_TYPES + [{"name": "", "name_en": "", "worthy": True, "hint": "x"}]
        with pytest.raises(TaxonomyError):
            load_taxonomy({"version": 1, "types": types})

    def test_hint_en_none_allowed(self):
        types = [dict(VALID_TYPES[0], hint_en=None), VALID_TYPES[1], VALID_TYPES[2]]
        t = load_taxonomy({"version": 1, "types": types})
        assert t.types[0].hint_en is None

    def test_json_text_load(self):
        text = json.dumps({"version": 1, "types": VALID_TYPES}, ensure_ascii=False)
        t = load_taxonomy(text)
        assert t.version == 1

    def test_non_dict_rejected(self):
        with pytest.raises(TaxonomyError):
            load_taxonomy([1, 2, 3])

    def test_choice_criteria_uses_hint_en_with_fallback(self):
        # hint_en missing -> fall back to Chinese hint (documented precision tradeoff).
        types = [dict(VALID_TYPES[0], hint_en=None), VALID_TYPES[1], VALID_TYPES[2]]
        t = load_taxonomy({"version": 1, "types": types})
        criteria = t.choice_criteria()
        assert criteria["monologue"] == types[0]["hint"]
        assert criteria["singing"] == "hint singing"


class TestWorthyMapping:
    def test_worthy_lookup_by_effective_key(self):
        t = load_taxonomy({"version": 1, "types": VALID_TYPES})
        assert worthy_of(t, "monologue") is True
        assert worthy_of(t, "singing") is False

    def test_unknown_key_raises(self):
        t = load_taxonomy({"version": 1, "types": VALID_TYPES})
        with pytest.raises(KeyError):
            worthy_of(t, "nonexistent")


class TestBuiltinTaxonomy:
    def test_builtin_has_11_types_with_expected_keys(self):
        t = builtin_taxonomy()
        keys = [effective_key(x) for x in t.types]
        assert len(keys) == 11
        assert set(keys) == {
            "monologue", "sales_pitch", "interview", "commentary", "tutorial",
            "drama", "singing", "dance", "gameplay", "music_visual", "other",
        }

    def test_builtin_worthy_flags(self):
        t = builtin_taxonomy()
        unworthy = {effective_key(x) for x in t.types if not x.worthy}
        assert unworthy == {"singing", "dance", "gameplay", "music_visual"}

    def test_builtin_all_have_hint_and_hint_en(self):
        t = builtin_taxonomy()
        assert all(x.hint and x.hint_en for x in t.types)

    def test_builtin_version_positive(self):
        assert builtin_taxonomy().version >= 1
