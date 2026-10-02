"""Smoke-test all provider API keys with one minimal request each (docs/07 §3).

First thing before any development: verify each key actually works so we don't
discover a broken key hours in. Prints key NAMES and status only — never values.

Usage: uv run python scripts/smoke_keys.py
Exit code 1 if any required check fails.
"""

from __future__ import annotations

import sys
import time

import httpx

from sidecar.config import ConfigError, Settings, load_settings

TIMEOUT = httpx.Timeout(30.0)


def check_dashscope(s: Settings) -> tuple[bool, str]:
    body = {
        "model": s.dashscope_vlm_model,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 8,
    }
    r = _post(f"{s.dashscope_base_url}/chat/completions", s.dashscope_api_key, body)
    text = r.json()["choices"][0]["message"].get("content", "")
    return f"model={r.json()['model']} reply={text!r}"


def check_typesafe(s: Settings) -> tuple[bool, str]:
    body = {
        "state": "A user reports their order #1234 arrived damaged and asks for a refund.",
        "model": s.jev_model,
        "questions": {
            "sentiment": {
                "type": "choice",
                "instructions": "Is this message urgent for support?",
                "criteria": {"yes": "Needs quick handling", "no": "Can wait"},
            }
        },
    }
    r = _post(f"{s.typesafe_base_url}/v1/systemone", s.typesafe_api_key, body)
    answer = r.json()["answers"]["sentiment"]
    usage = r.json().get("usage", {})
    return (
        f"model={r.json()['model']} choice={answer['choice']} "
        f"confidence={answer['confidence']:.2f} tokens={usage.get('input_tokens')}->"
        f"{usage.get('output_tokens')}"
    )


def check_zhipu(s: Settings) -> tuple[bool, str]:
    body = {
        "model": s.zhipu_vlm_model,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 8,
    }
    r = _post(f"{s.zhipu_base_url}/chat/completions", s.zhipu_api_key, body)
    text = r.json()["choices"][0]["message"].get("content", "")
    return f"model={r.json().get('model', s.zhipu_vlm_model)} reply={text!r}"


def check_ark(s: Settings) -> tuple[bool, str]:
    body = {
        "model": s.ark_default_model,
        "messages": [{"role": "user", "content": "Reply with exactly: OK"}],
        "max_tokens": 8,
    }
    r = _post(f"{s.ark_base_url}/chat/completions", s.ark_api_key, body)
    text = r.json()["choices"][0]["message"].get("content", "")
    return f"model={s.ark_default_model} reply={text!r}"


def _post(url: str, api_key: str | None, body: dict) -> httpx.Response:
    if not api_key:
        raise RuntimeError("key not set")
    r = httpx.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=body,
        timeout=TIMEOUT,
    )
    r.raise_for_status()
    return r


def run_one(name: str, fn, s: Settings) -> str:
    start = time.perf_counter()
    try:
        detail = fn(s)
        return f"PASS  {name:<12} {time.perf_counter() - start:5.1f}s  {detail}"
    except Exception as e:  # noqa: BLE001 — smoke test reports, never crashes
        msg = str(e).replace("\n", " ")[:180]
        return f"FAIL  {name:<12} {time.perf_counter() - start:5.1f}s  {msg}"


def main() -> int:
    try:
        s = load_settings(_clean_env())
    except ConfigError as e:
        print(f"CONFIG FAIL: {e}")
        return 2

    checks = [
        ("dashscope", check_dashscope, True),
        ("typesafe", check_typesafe, True),
        ("zhipu", check_zhipu, s.zhipu_api_key is not None),
        ("ark", check_ark, s.ark_api_key is not None),
    ]
    lines = [
        run_one(name, fn, s) if enabled else f"SKIP  {name:<12} key not set"
        for name, fn, enabled in checks
    ]
    print("\n".join(lines))
    return 1 if any(line.startswith("FAIL") for line in lines) else 0


def _clean_env() -> dict[str, str]:
    """Only pass relevant env vars through (avoid pytest-injected noise)."""
    import os

    keys = (
        "DASHSCOPE_API_KEY", "TYPESAFE_API_KEY", "ZHIPU_API_KEY", "ARK_API_KEY",
        "DOUYIN_COOKIE", "DASHSCOPE_BASE_URL", "DASHSCOPE_VLM_MODEL", "ZHIPU_BASE_URL",
        "ZHIPU_VLM_MODEL", "ARK_BASE_URL", "ARK_DEFAULT_MODEL", "TYPESAFE_BASE_URL",
        "JEV_MODEL",
    )
    return {k: os.environ[k] for k in keys if k in os.environ}


if __name__ == "__main__":
    sys.exit(main())
