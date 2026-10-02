"""Settings for the Phase 0 CLI.

Key read order (docs/04 §4): process environment -> project-root .env.local.
Required keys missing/blank -> ConfigError naming the keys (never their values).
base_url / model values are configuration, not code: defaults live here and can
be overridden via environment variables (no hardcoding at call sites).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from pydantic import BaseModel

from sidecar.envfile import EnvFileError, parse_env_file

# Repo root = parent of the sidecar package (Phase 0 CLI layout).
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env.local"

REQUIRED_KEYS = ("DASHSCOPE_API_KEY", "TYPESAFE_API_KEY")


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


class Settings(BaseModel):
    """Immutable runtime settings. Secrets never appear in __repr__ logs."""

    model_config = {"frozen": True}

    # API keys (required)
    dashscope_api_key: str
    typesafe_api_key: str
    # API keys (optional)
    zhipu_api_key: str | None = None
    ark_api_key: str | None = None
    douyin_cookie: str | None = None

    # Provider endpoints & models — overridable via env, defaults only here.
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_native_base_url: str = "https://dashscope.aliyuncs.com"
    dashscope_vlm_model: str = "qwen3-vl-flash"
    zhipu_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    zhipu_vlm_model: str = "glm-4.6v-flash"
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    ark_default_model: str = "doubao-seed-1-6-flash-250828"
    typesafe_base_url: str = "https://api.typesafe.ai"
    jev_model: str = "jev-latest"

    # Classification knobs (docs/03 §3.0 defaults; advanced settings later)
    frame_count: int = 10
    frame_max_edge: int = 512
    confidence_threshold: float = 0.85


_ENV_TO_SETTING: dict[str, str] = {
    "DASHSCOPE_BASE_URL": "dashscope_base_url",
    "DASHSCOPE_NATIVE_BASE_URL": "dashscope_native_base_url",
    "DASHSCOPE_VLM_MODEL": "dashscope_vlm_model",
    "ZHIPU_BASE_URL": "zhipu_base_url",
    "ZHIPU_VLM_MODEL": "zhipu_vlm_model",
    "ARK_BASE_URL": "ark_base_url",
    "ARK_DEFAULT_MODEL": "ark_default_model",
    "TYPESAFE_BASE_URL": "typesafe_base_url",
    "JEV_MODEL": "jev_model",
}

_KEY_FIELDS: dict[str, str] = {
    "DASHSCOPE_API_KEY": "dashscope_api_key",
    "TYPESAFE_API_KEY": "typesafe_api_key",
    "ZHIPU_API_KEY": "zhipu_api_key",
    "ARK_API_KEY": "ark_api_key",
    "DOUYIN_COOKIE": "douyin_cookie",
}


def load_settings(
    env: Mapping[str, str],
    env_file: Path | None = DEFAULT_ENV_FILE,
) -> Settings:
    """Merge .env.local with the real environment (env wins), then validate.

    Raises ConfigError naming missing required keys. Unknown keys in the env
    file are ignored (OSS_* etc. are not needed by the Phase 0 CLI).
    """
    try:
        file_vars = parse_env_file(env_file) if env_file is not None else {}
    except EnvFileError as e:
        raise ConfigError(f"cannot read env file: {e}") from e

    merged: dict[str, str] = {**file_vars, **{k: v for k, v in env.items() if v}}

    missing = [k for k in REQUIRED_KEYS if not merged.get(k)]
    if missing:
        raise ConfigError(
            f"missing required keys (set env vars or {env_file}): {', '.join(missing)}"
        )

    values: dict[str, object] = {}
    for env_key, field in _KEY_FIELDS.items():
        raw = merged.get(env_key)
        values[field] = raw or None
    for env_key, field in _ENV_TO_SETTING.items():
        if merged.get(env_key):
            values[field] = merged[env_key]
    values["dashscope_api_key"] = merged["DASHSCOPE_API_KEY"]
    values["typesafe_api_key"] = merged["TYPESAFE_API_KEY"]
    return Settings(**values)  # type: ignore[arg-type]
