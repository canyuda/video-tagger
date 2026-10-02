"""Tests for settings loading: env vars take precedence over .env.local (docs/04 §4)."""

import pytest

from sidecar.config import ConfigError, load_settings


class TestLoadSettings:
    def test_required_keys_from_file(self, tmp_path):
        f = tmp_path / ".env.local"
        f.write_text("DASHSCOPE_API_KEY=sk-a\nTYPESAFE_API_KEY=ts-b\n")
        s = load_settings({}, env_file=f)
        assert s.dashscope_api_key == "sk-a"
        assert s.typesafe_api_key == "ts-b"

    def test_env_overrides_file(self, tmp_path):
        f = tmp_path / ".env.local"
        f.write_text("DASHSCOPE_API_KEY=from-file\nTYPESAFE_API_KEY=ts-b\n")
        s = load_settings({"DASHSCOPE_API_KEY": "from-env"}, env_file=f)
        assert s.dashscope_api_key == "from-env"

    def test_optional_keys_default_to_none(self, tmp_path):
        f = tmp_path / ".env.local"
        f.write_text("DASHSCOPE_API_KEY=a\nTYPESAFE_API_KEY=b\n")
        s = load_settings({}, env_file=f)
        assert s.zhipu_api_key is None
        assert s.ark_api_key is None
        assert s.douyin_cookie is None

    def test_missing_required_key_lists_all_missing_names(self, tmp_path):
        # Fail fast: error must name the missing keys, never include values.
        f = tmp_path / ".env.local"
        f.write_text("ZHIPU_API_KEY=z\n")
        with pytest.raises(ConfigError) as ei:
            load_settings({}, env_file=f)
        msg = str(ei.value)
        assert "DASHSCOPE_API_KEY" in msg
        assert "TYPESAFE_API_KEY" in msg

    def test_no_env_file_and_no_env_vars_fails(self, tmp_path):
        with pytest.raises(ConfigError):
            load_settings({}, env_file=tmp_path / ".env.local")

    def test_blank_required_key_treated_as_missing(self, tmp_path):
        f = tmp_path / ".env.local"
        f.write_text("DASHSCOPE_API_KEY=\nTYPESAFE_API_KEY=b\n")
        with pytest.raises(ConfigError, match="DASHSCOPE_API_KEY"):
            load_settings({}, env_file=f)

    def test_unknown_keys_ignored(self, tmp_path):
        f = tmp_path / ".env.local"
        f.write_text("DASHSCOPE_API_KEY=a\nTYPESAFE_API_KEY=b\nOSS_ENDPOINT=x\n")
        s = load_settings({}, env_file=f)
        assert s.dashscope_api_key == "a"

    def test_base_url_overrides_via_env(self, tmp_path):
        f = tmp_path / ".env.local"
        f.write_text("DASHSCOPE_API_KEY=a\nTYPESAFE_API_KEY=b\n")
        s = load_settings({"DASHSCOPE_BASE_URL": "https://proxy.example/v1"}, env_file=f)
        assert s.dashscope_base_url == "https://proxy.example/v1"

    def test_default_base_urls_and_models(self, tmp_path):
        f = tmp_path / ".env.local"
        f.write_text("DASHSCOPE_API_KEY=a\nTYPESAFE_API_KEY=b\n")
        s = load_settings({}, env_file=f)
        assert s.dashscope_base_url == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert s.dashscope_native_base_url == "https://dashscope.aliyuncs.com"
        assert s.dashscope_vlm_model == "qwen3-vl-flash"
        assert s.zhipu_base_url == "https://open.bigmodel.cn/api/paas/v4"
        assert s.typesafe_base_url == "https://api.typesafe.ai"
        assert s.jev_model == "jev-latest"
