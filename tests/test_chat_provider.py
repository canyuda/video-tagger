"""Contract tests for the chat provider via httpx MockTransport (zero-cost CI
layer, D38). Canned bodies here become the replay fixtures; real-call
recordings replace them per D57 once the pipeline runs."""

import json

import httpx
import pytest

from sidecar.providers.chat import ChatProvider, parse_json_content
from sidecar.providers.errors import ProviderError

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


def make_provider(handler, **kw) -> tuple[ChatProvider, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    provider = ChatProvider(
        base_url="https://api.example.com/v1",
        api_key="secret-key",
        model="test-model",
        transport=httpx.MockTransport(transport),
        retry_policy=None,  # tests exercise single-attempt behavior explicitly
        **kw,
    )
    return provider, requests


def ok_body(content: str) -> bytes:
    return json.dumps(
        {
            "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    ).encode()


class TestChat:
    def test_success_parses_content_and_usage(self):
        p, reqs = make_provider(lambda r: httpx.Response(200, content=ok_body("hello")))
        result = p.chat([{"role": "user", "content": "hi"}])
        assert result.content == "hello"
        assert result.input_tokens == 10 and result.output_tokens == 5
        sent = json.loads(reqs[0].read())
        assert sent["model"] == "test-model"
        assert reqs[0].headers["Authorization"] == "Bearer secret-key"

    def test_json_schema_sent_when_requested(self):
        p, reqs = make_provider(lambda r: httpx.Response(200, content=ok_body("{}")))
        p.chat([{"role": "user", "content": "hi"}], response_json_schema=SCHEMA)
        sent = json.loads(reqs[0].read())
        assert sent["response_format"]["type"] == "json_schema"
        assert sent["response_format"]["json_schema"]["schema"] == SCHEMA

    def test_http_401_is_terminal(self):
        p, _ = make_provider(
            lambda r: httpx.Response(401, text='{"error":"bad key"}')
        )
        with pytest.raises(ProviderError) as ei:
            p.chat([{"role": "user", "content": "hi"}])
        assert not ei.value.retryable
        assert ei.value.code == "CHAT-401"

    def test_http_500_is_retryable(self):
        p, _ = make_provider(lambda r: httpx.Response(500, text="boom"))
        with pytest.raises(ProviderError) as ei:
            p.chat([{"role": "user", "content": "hi"}])
        assert ei.value.retryable


class TestRequestJson:
    def test_happy_path_returns_dict(self):
        p, _ = make_provider(lambda r: httpx.Response(200, content=ok_body('{"ok": true}')))
        out = p.request_json([{"role": "user", "content": "go"}], SCHEMA)
        assert out.data == {"ok": True}
        assert out.input_tokens == 10 and out.output_tokens == 5

    def test_code_fenced_json_tolerated(self):
        p, _ = make_provider(
            lambda r: httpx.Response(200, content=ok_body("```json\n{\"ok\": false}\n```"))
        )
        assert p.request_json([{"role": "user", "content": "go"}], SCHEMA).data == {"ok": False}

    def test_one_repair_round_trip_on_bad_json(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            body = "NOT JSON AT ALL" if calls["n"] == 1 else '{"ok": true}'
            return httpx.Response(200, content=ok_body(body))

        p, _ = make_provider(handler)
        out = p.request_json([{"role": "user", "content": "go"}], SCHEMA)
        assert out.data == {"ok": True}
        assert calls["n"] == 2
        assert out.input_tokens == 20  # 10 + 10 summed across the repair round-trip

    def test_two_bad_jsons_terminal(self):
        p, _ = make_provider(lambda r: httpx.Response(200, content=ok_body("garbage")))
        with pytest.raises(ProviderError) as ei:
            p.request_json([{"role": "user", "content": "go"}], SCHEMA)
        assert not ei.value.retryable
        assert "BADJSON" in ei.value.code

    def test_json_schema_rejection_degrades_to_json_object(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(400, text='{"error":"response_format unsupported"}')
            return httpx.Response(200, content=ok_body('{"ok": true}'))

        p, reqs = make_provider(handler)
        result = p.request_json([{"role": "user", "content": "go"}], SCHEMA)
        assert result.data == {"ok": True}
        second = json.loads(reqs[1].read())
        assert second["response_format"] == {"type": "json_object"}


class TestParseJsonContent:
    def test_plain(self):
        assert parse_json_content('{"a": 1}') == {"a": 1}

    def test_fenced(self):
        assert parse_json_content('```json\n{"a": 1}\n```') == {"a": 1}

    def test_non_object_raises(self):
        with pytest.raises(ValueError):
            parse_json_content("[1, 2]")
