"""Replay of REAL recorded provider calls (D57): recorded fixtures go through
the actual client classes. This validates that our contract tests use the
providers' true response shapes, not just our reading of the docs.

Fixtures are recorded by scripts/record_fixtures.py (real API, sanitized:
auth headers dropped, ephemeral OSS credentials and signed URLs scrubbed).
Tests skip when fixtures have not been recorded yet.
"""

import json
from pathlib import Path

import httpx
import pytest

from sidecar.providers.asr import AsrProvider
from sidecar.providers.chat import ChatProvider
from sidecar.providers.jev import JevClient

FIXTURE_DIR = Path(__file__).parent / "fixtures"


def load(name: str) -> list[dict]:
    path = FIXTURE_DIR / f"{name}.json"
    if not path.is_file():
        pytest.skip(f"fixture {name} not recorded (run scripts/record_fixtures.py)")
    return json.loads(path.read_text(encoding="utf-8"))


def replay_transport(entries: list[dict]):
    """Serve recorded responses in order; assert method matches the recording."""

    def transport(request: httpx.Request) -> httpx.Response:
        idx = transport.calls
        transport.calls += 1
        entry = entries[idx]
        assert request.method == entry["method"], (
            f"call #{idx}: expected {entry['method']}, client sent {request.method}"
        )
        return httpx.Response(
            entry["status"],
            json=entry["response_body"] if entry["response_body"] is not None else None,
        )

    transport.calls = 0
    return httpx.MockTransport(transport)


class TestChatStructuredReplay:
    def test_real_qwen3vl_structured_output(self):
        entries = load("chat_structured_qwen3vl")
        provider = ChatProvider(
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            api_key="test", model="qwen3-vl-flash",
            transport=replay_transport(entries), retry_policy=None,
        )
        schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
        out = provider.request_json(
            [{"role": "user", "content": "fixture replay"}], schema
        )
        assert isinstance(out.data.get("answer"), str)
        assert entries[0]["request_body"]["response_format"]["type"] == "json_schema"


class TestJevChoiceReplay:
    def test_real_jev_choice_answer(self):
        entries = load("jev_choice")
        client = JevClient(
            base_url="https://api.typesafe.ai", api_key="test", model="jev-latest",
            transport=replay_transport(entries), retry_policy=None,
        )
        result = client.ask({"ticket": "fixture replay"}, {"urgency": {}})
        answer = result.choice("urgency")
        assert 0.0 <= answer.confidence <= 1.0
        assert answer.choice in answer.probabilities
        # request shape recorded from the real API
        body = entries[0]["request_body"]
        assert body["model"].startswith("jev-")
        assert "urgency" in body["questions"]


class TestAsrFlowReplay:
    def test_real_paraformer_flow(self, tmp_path):
        entries = load("asr_paraformer_flow")
        audio = tmp_path / "audio_sample.mp3"
        audio.write_bytes(b"fixture-replay-placeholder")

        provider = AsrProvider(
            base_url="https://dashscope.aliyuncs.com", api_key="test",
            poll_interval=0.0, poll_timeout=5.0,
            transport=replay_transport(entries), retry_policy=None,
        )
        sentences = provider.transcribe(audio)
        assert len(sentences) >= 1
        assert all(s.text for s in sentences)
        # the upload step really was a multipart POST to the OSS host
        upload = entries[1]
        assert upload["url"].startswith("https://dashscope-file-")
        assert "_raw_multipart_scrubbed" in upload["request_body"]
