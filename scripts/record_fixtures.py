"""Record real provider call fixtures for contract tests (D57).

Runs one minimal REAL call per provider through a sanitizing recording
transport and writes tests/fixtures/*.json. Secrets never land in fixtures:
Authorization headers are dropped, base64 images replaced by placeholders,
temporary OSS upload credentials (policy/signature) are scrubbed.

Usage: uv run python scripts/record_fixtures.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import httpx

from sidecar.config import load_settings

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "tests" / "fixtures"
DATA_URL_RE = re.compile(r"data:image/[a-zA-Z]+;base64,[A-Za-z0-9+/=]+")
SECRET_FORM_KEYS = ("Signature", "policy", "OSSAccessKeyId")
SECRET_JSON_KEYS = ("policy", "signature", "oss_access_key_id", "OSSAccessKeyId")


class RecordingTransport(httpx.BaseTransport):
    """Delegate to a real transport, recording sanitized request/response."""

    def __init__(self) -> None:
        self._real = httpx.HTTPTransport()
        self.entries: list[dict] = []

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._real.handle_request(request)
        response.read()  # eager read so the recorder can inspect the body
        request.read()
        entry = {
            "method": request.method,
            "url": _sanitize_url(str(request.url)),
            "request_headers": _sanitize_headers(request.headers),
            "request_body": _sanitize_body(request.content),
            "status": response.status_code,
            "response_body": _sanitize_body(response.content),
        }
        self.entries.append(entry)
        return response


def _sanitize_url(url: str) -> str:
    # Drop query strings: signed OSS/result URLs carry temporary credentials.
    return url.split("?", 1)[0]


def _sanitize_headers(headers: httpx.Headers) -> dict:
    return {
        k: ("<redacted>" if k.lower() == "authorization" else v)
        for k, v in headers.items()
        if k.lower() in ("authorization", "content-type", "x-dashscope-async",
                         "x-dashscope-ossresourceresolve")
    }


def _sanitize_body(content: bytes | None) -> object:
    if not content:
        return None
    text = content.decode("utf-8", errors="replace")
    text = DATA_URL_RE.sub("<image-b64:{len}>", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # multipart form (OSS upload): scrub credential fields
        parts = []
        for chunk in text.split("name="):
            if any(k in chunk for k in SECRET_FORM_KEYS):
                continue
            parts.append(chunk[:200])
        return {"_raw_multipart_scrubbed": "\n".join(parts)[:1000]}
    return _scrub_credentials(parsed)


def _scrub_credentials(obj):
    """Recursively replace ephemeral credentials in parsed JSON: known key
    names, and any URL carrying an OSS query signature."""
    if isinstance(obj, dict):
        return {
            k: ("<ephemeral-redacted>" if k in SECRET_JSON_KEYS else _scrub_credentials(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_scrub_credentials(item) for item in obj]
    if isinstance(obj, str) and "Signature=" in obj:
        return obj.split("?", 1)[0]
    return obj


def write_fixture(name: str, entries: list[dict]) -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / f"{name}.json"
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def record_chat_structured(settings) -> Path:
    from sidecar.providers.chat import ChatProvider

    transport = RecordingTransport()
    chat = ChatProvider(
        base_url=settings.dashscope_base_url,
        api_key=settings.dashscope_api_key,
        model=settings.dashscope_vlm_model,
        transport=transport,
    )
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}, "flag": {"type": "boolean"}},
        "required": ["answer", "flag"],
        "additionalProperties": False,
    }
    out = chat.request_json(
        [{"role": "user", "content": "Answer with JSON: answer='ok', flag=true"}], schema
    )
    assert out.data.get("answer"), f"unexpected chat output: {out.data}"
    return write_fixture("chat_structured_qwen3vl", transport.entries)


def record_jev_choice(settings) -> Path:
    from sidecar.providers.jev import JevClient

    transport = RecordingTransport()
    jev = JevClient(
        base_url=settings.typesafe_base_url,
        api_key=settings.typesafe_api_key,
        model=settings.jev_model,
        transport=transport,
    )
    result = jev.ask(
        {"ticket": "My order #1234 arrived damaged and I want a refund."},
        {
            "urgency": {
                "type": "choice",
                "instructions": "How urgent is this support ticket?",
                "criteria": {
                    "high": "Needs immediate handling",
                    "normal": "Can wait a day",
                    "low": "Informational",
                },
            }
        },
    )
    assert "urgency" in result.answers
    return write_fixture("jev_choice", transport.entries)


def record_asr_flow(settings, audio_path: Path) -> Path:
    from sidecar.providers.asr import AsrProvider

    transport = RecordingTransport()
    asr = AsrProvider(
        base_url=settings.dashscope_native_base_url,
        api_key=settings.dashscope_api_key,
        transport=transport,
    )
    sentences = asr.transcribe(audio_path)
    assert sentences, "empty transcription"
    return write_fixture("asr_paraformer_flow", transport.entries)


def main() -> int:
    settings = load_settings(os.environ)
    written = [
        record_chat_structured(settings),
        record_jev_choice(settings),
    ]
    # ASR: reuse the L2 sample if present, else synthesize a 3s tone file.
    sample = None
    work = Path("data/work")
    if work.is_dir():
        samples = sorted(work.glob("*/audio_sample.mp3"))
        sample = samples[0] if samples else None
    if sample is None:
        import subprocess
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            sample = Path(f.name)
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "sine=frequency=440:duration=3", str(sample)],
            check=True,
        )
    written.append(record_asr_flow(settings, sample))
    for path in written:
        print(f"fixture: {path} ({path.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
