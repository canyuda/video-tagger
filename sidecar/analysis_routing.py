"""Analysis routing (D58): classification type -> template + model.

The routing table is user-editable config (settings key `analysis_routing`).
Classification is the PRE-STEP of analysis: it gates worthiness (D09/D14),
then routes to the prompt template and model tier suited to the content.

Table shape (JSON):
    {
      "_default": {"template_id": "tpl-general", "model": "qwen3-vl-flash"},
      "monologue": {"template_id": "tpl-general", "model": "qwen3-vl-flash"},
      "tutorial":  {"template_id": "tpl-knowledge", "model": "qwen3-vl-plus"},
      ...
    }
Keys are taxonomy effective keys (name_en, D43) plus the `_default` fallback.
Unknown types fall back to `_default`.
"""

from __future__ import annotations

from typing import Any

ROUTING_SETTING_KEY = "analysis_routing"

KNOWN_MODELS = (
    "qwen3-vl-flash",   # 百炼 便宜档
    "qwen3-vl-plus",    # 百炼 质量档
    "glm-4.6v-flash",   # 智谱 免费档
)

DEFAULT_ROUTING: dict[str, dict[str, str]] = {
    "_default": {"template_id": "tpl-general", "model": "qwen3-vl-flash"},
    "monologue": {"template_id": "tpl-general", "model": "qwen3-vl-flash"},
    "sales_pitch": {"template_id": "tpl-sales", "model": "qwen3-vl-flash"},
    "interview": {"template_id": "tpl-general", "model": "qwen3-vl-plus"},
    "commentary": {"template_id": "tpl-factcheck", "model": "qwen3-vl-flash"},
    "tutorial": {"template_id": "tpl-knowledge", "model": "qwen3-vl-plus"},
    "drama": {"template_id": "tpl-general", "model": "qwen3-vl-plus"},
    # 无价值类型不路由（会被门控拦截），仅兜底存在
    "singing": {"template_id": "tpl-general", "model": "qwen3-vl-flash"},
    "dance": {"template_id": "tpl-general", "model": "qwen3-vl-flash"},
    "gameplay": {"template_id": "tpl-general", "model": "qwen3-vl-flash"},
    "music_visual": {"template_id": "tpl-general", "model": "qwen3-vl-flash"},
    "other": {"template_id": "tpl-general", "model": "qwen3-vl-flash"},
}


class RoutingError(Exception):
    pass


def normalize_routing(raw: Any) -> dict[str, dict[str, str]]:
    """Validate + normalize a routing table from untrusted JSON. Fail fast
    with RoutingError on structural problems (fail-fast, no silent repair)."""
    if not isinstance(raw, dict):
        raise RoutingError("routing must be a JSON object")
    normalized: dict[str, dict[str, str]] = {}
    for type_key, entry in raw.items():
        if not isinstance(type_key, str) or not type_key.strip():
            raise RoutingError(f"routing key must be a non-empty string: {type_key!r}")
        if not isinstance(entry, dict):
            raise RoutingError(f"routing entry for {type_key!r} must be an object")
        template_id = entry.get("template_id")
        model = entry.get("model")
        if not isinstance(template_id, str) or not template_id.strip():
            raise RoutingError(f"{type_key!r}: template_id is required")
        if not isinstance(model, str) or not model.strip():
            raise RoutingError(f"{type_key!r}: model is required")
        normalized[type_key.strip()] = {
            "template_id": template_id.strip(),
            "model": model.strip(),
        }
    if "_default" not in normalized:
        raise RoutingError("routing requires a '_default' fallback entry")
    return normalized


def recommend(
    routing: dict[str, dict[str, str]], type_key: str | None
) -> dict[str, str]:
    """Route one classification result to {template_id, model}. Falls back to
    _default for unknown/None types (fail-open, never blocks analysis)."""
    if not routing:
        routing = DEFAULT_ROUTING
    entry = routing.get(type_key or "") if type_key else None
    if entry is None:
        entry = routing.get("_default") or DEFAULT_ROUTING["_default"]
    return {"template_id": entry["template_id"], "model": entry["model"]}
