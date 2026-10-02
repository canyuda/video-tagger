"""Contract tests for the paraformer-v2 ASR provider (MockTransport replay of
the documented upload/submit/poll/download flow)."""

import json

import httpx
import pytest

from sidecar.providers.asr import AsrProvider, parse_transcription
from sidecar.providers.errors import ProviderError

TRANSCRIPTION_BODY = {
    "transcripts": [
        {
            "sentences": [
                {"text": "今天聊聊", "begin_time": 100, "end_time": 900},
                {"text": "怎么涨粉", "begin_time": 900, "end_time": 2000},
            ]
        }
    ]
}


def make_provider(handler, **kw) -> AsrProvider:
    return AsrProvider(
        base_url="https://api.example.com",
        api_key="secret-key",
        poll_interval=0.0,
        poll_timeout=1.0,
        transport=httpx.MockTransport(handler),
        retry_policy=None,
        **kw,
    )


def policy_response() -> dict:
    return {
        "data": {
            "upload_host": "https://oss.example.com",
            "policy": "base64policy==",
            "signature": "sig==",
            "upload_dir": "dashscope-instant/abc",
            "oss_access_key_id": "AKID",
            "x_oss_object_acl": "private",
            "x_oss_forbid_overwrite": "true",
        }
    }


class TestTranscribeFlow:
    def test_full_chain(self, tmp_path):
        audio = tmp_path / "sample.mp3"
        audio.write_bytes(b"ID3-fake-mp3")
        poll_count = {"n": 0}
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            url, method = str(request.url), request.method
            if "action=getPolicy" in url:
                seen["policy_auth"] = request.headers.get("Authorization")
                return httpx.Response(200, json=policy_response())
            if url.startswith("https://oss.example.com"):
                seen["upload_body"] = request.content[:200]
                return httpx.Response(200)
            if url.endswith("/transcription"):
                body = json.loads(request.read())
                seen["submit_body"] = body
                seen["submit_headers"] = dict(request.headers)
                submit_out = {"task_id": "T1", "task_status": "PENDING"}
                return httpx.Response(200, json={"output": submit_out})
            if "/tasks/T1" in url:
                poll_count["n"] += 1
                if poll_count["n"] < 2:
                    return httpx.Response(200, json={"output": {"task_status": "RUNNING"}})
                seen["poll_headers"] = dict(request.headers)
                return httpx.Response(
                    200,
                    json={
                        "output": {
                            "task_status": "SUCCEEDED",
                            "results": [
                                {"subtask_status": "SUCCEEDED", "transcription_url": "https://result.example.com/x.json"}
                            ],
                        }
                    },
                )
            if url == "https://result.example.com/x.json":
                return httpx.Response(200, json=TRANSCRIPTION_BODY)
            return httpx.Response(500, text=f"unexpected {method} {url}")

        provider = make_provider(handler)
        sentences = provider.transcribe(audio)

        assert [s.text for s in sentences] == ["今天聊聊", "怎么涨粉"]
        assert sentences[0].begin_ms == 100
        # oss:// URL constructed from upload_dir + filename
        assert seen["submit_body"]["input"]["file_urls"] == ["oss://dashscope-instant/abc/sample.mp3"]
        assert seen["submit_body"]["model"] == "paraformer-v2"
        assert seen["submit_headers"].get("x-dashscope-async") == "enable"
        assert seen["submit_headers"].get("x-dashscope-ossresourceresolve") == "enable"
        assert seen["policy_auth"] == "Bearer secret-key"
        # polling looped once through RUNNING
        assert poll_count["n"] == 2

    def test_failed_task_is_terminal(self, tmp_path):
        audio = tmp_path / "s.mp3"
        audio.write_bytes(b"x")

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "action=getPolicy" in url:
                return httpx.Response(200, json=policy_response())
            if url.startswith("https://oss.example.com"):
                return httpx.Response(200)
            if url.endswith("/transcription"):
                return httpx.Response(200, json={"output": {"task_id": "T2"}})
            if "/tasks/T2" in url:
                return httpx.Response(
                    200,
                    json={"output": {"task_status": "FAILED", "message": "bad audio"}},
                )
            return httpx.Response(500)

        with pytest.raises(ProviderError) as ei:
            make_provider(handler).transcribe(audio)
        assert not ei.value.retryable

    def test_subtask_failure_is_terminal(self, tmp_path):
        audio = tmp_path / "s.mp3"
        audio.write_bytes(b"x")

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if "action=getPolicy" in url:
                return httpx.Response(200, json=policy_response())
            if url.startswith("https://oss.example.com"):
                return httpx.Response(200)
            if url.endswith("/transcription"):
                return httpx.Response(200, json={"output": {"task_id": "T3"}})
            if "/tasks/T3" in url:
                return httpx.Response(
                    200,
                    json={
                        "output": {
                            "task_status": "SUCCEEDED",
                            "results": [{"subtask_status": "FAILED", "code": "E1", "message": "m"}],
                        }
                    },
                )
            return httpx.Response(500)

        with pytest.raises(ProviderError) as ei:
            make_provider(handler).transcribe(audio)
        assert "SUBTASK" in ei.value.code


class TestParseTranscription:
    def test_sentences_parsed(self):
        result = parse_transcription(TRANSCRIPTION_BODY)
        assert len(result) == 2

    def test_empty_transcript_ok(self):
        assert parse_transcription({"transcripts": [{"sentences": []}]}) == []
        assert parse_transcription({"transcripts": []}) == []

    def test_missing_transcripts_terminal(self):
        with pytest.raises(ProviderError):
            parse_transcription({"unexpected": True})
