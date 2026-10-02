"""Contract tests for the Jev decision provider via MockTransport."""

import json

import httpx
import pytest

from sidecar.providers.errors import ProviderError
from sidecar.providers.jev import JevClient

OK_BODY = {
    "model": "jev-1.13.0",
    "answers": {
        "video_type": {
            "type": "choice",
            "choice": "monologue",
            "confidence": 0.92,
            "probabilities": {"monologue": 0.92, "singing": 0.05, "other": 0.03},
        }
    },
    "usage": {"input_tokens": 328, "output_tokens": 32},
}


def make_client(handler) -> tuple[JevClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def transport(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = JevClient(
        base_url="https://api.example.com",
        api_key="secret-key",
        model="jev-latest",
        transport=httpx.MockTransport(transport),
        retry_policy=None,
    )
    return client, requests


class TestJevAsk:
    def test_success_parses_choice_answer(self):
        c, reqs = make_client(
            lambda r: httpx.Response(200, json=OK_BODY)
        )
        result = c.ask({"observed": "person facing camera"}, {"q": {"type": "choice"}})
        answer = result.choice("video_type")
        assert answer.choice == "monologue"
        assert answer.confidence == pytest.approx(0.92)
        assert answer.probabilities["singing"] == pytest.approx(0.05)
        assert result.input_tokens == 328

        sent = json.loads(reqs[0].read())
        assert sent["model"] == "jev-latest"
        assert sent["state"] == {"observed": "person facing camera"}
        assert reqs[0].url.path == "/v1/systemone"

    def test_missing_question_answer_is_terminal(self):
        c, _ = make_client(lambda r: httpx.Response(200, json=OK_BODY))
        with pytest.raises(ProviderError) as ei:
            c.ask("state", {"q": {}}).choice("nonexistent")
        assert not ei.value.retryable

    def test_http_429_is_retryable(self):
        c, _ = make_client(lambda r: httpx.Response(429, text="slow down"))
        with pytest.raises(ProviderError) as ei:
            c.ask("s", {})
        assert ei.value.retryable
        assert ei.value.code == "DEC-429"

    def test_http_529_overloaded_is_retryable(self):
        c, _ = make_client(lambda r: httpx.Response(529, text="overloaded"))
        with pytest.raises(ProviderError) as ei:
            c.ask("s", {})
        assert ei.value.retryable

    def test_http_401_is_terminal(self):
        c, _ = make_client(lambda r: httpx.Response(401, text="nope"))
        with pytest.raises(ProviderError) as ei:
            c.ask("s", {})
        assert not ei.value.retryable

    def test_malformed_body_is_terminal(self):
        c, _ = make_client(lambda r: httpx.Response(200, text="not json"))
        with pytest.raises(ProviderError):
            c.ask("s", {})
