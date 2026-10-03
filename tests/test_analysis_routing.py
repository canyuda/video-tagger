"""Tests for analysis routing (D58): type -> template + model."""

import pytest

from sidecar.analysis_routing import (
    DEFAULT_ROUTING,
    ROUTING_SETTING_KEY,
    RoutingError,
    normalize_routing,
    recommend,
)


class TestNormalize:
    def test_valid_table_roundtrip(self):
        table = normalize_routing({
            "_default": {"template_id": "tpl-general", "model": "qwen3-vl-flash"},
            "tutorial": {"template_id": "tpl-knowledge", "model": "qwen3-vl-plus"},
        })
        assert table["tutorial"] == {"template_id": "tpl-knowledge", "model": "qwen3-vl-plus"}

    def test_strips_whitespace(self):
        table = normalize_routing({"_default": {"template_id": " x ", "model": " y "}})
        assert table["_default"] == {"template_id": "x", "model": "y"}

    @pytest.mark.parametrize("bad", [
        None, [], "x",
        {"monologue": "not-an-object"},
        {"monologue": {"template_id": "", "model": "x"}},
        {"monologue": {"template_id": "x"}},
        {"monologue": {"template_id": "x", "model": ""}},
        {"monologue": {"template_id": "x", "model": "y"}},  # no _default
        {"": {"template_id": "x", "model": "y"}},
    ])
    def test_invalid_tables_raise(self, bad):
        with pytest.raises(RoutingError):
            normalize_routing(bad)


class TestRecommend:
    def test_routes_known_type(self):
        assert recommend(DEFAULT_ROUTING, "tutorial")["template_id"] == "tpl-knowledge"
        assert recommend(DEFAULT_ROUTING, "tutorial")["model"] == "qwen3-vl-plus"

    def test_unknown_type_falls_back_to_default(self):
        assert recommend(DEFAULT_ROUTING, "nonexistent") == DEFAULT_ROUTING["_default"]
        assert recommend(DEFAULT_ROUTING, None) == DEFAULT_ROUTING["_default"]

    def test_empty_table_falls_back_to_builtin_default(self):
        assert recommend({}, "whatever") == DEFAULT_ROUTING["_default"]

    def test_sales_pitch_routes_to_sales_template(self):
        assert recommend(DEFAULT_ROUTING, "sales_pitch")["template_id"] == "tpl-sales"


class TestDefaults:
    def test_builtin_default_table_is_valid(self):
        assert normalize_routing(DEFAULT_ROUTING) == DEFAULT_ROUTING

    def test_all_11_types_covered(self):
        keys = {k for k in DEFAULT_ROUTING if k != "_default"}
        assert keys == {
            "monologue", "sales_pitch", "interview", "commentary", "tutorial",
            "drama", "singing", "dance", "gameplay", "music_visual", "other",
        }
