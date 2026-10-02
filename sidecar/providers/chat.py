"""OpenAI-compatible chat provider (DashScope qwen / Zhipu GLM, docs/04 §4).

All endpoint/model values come from Settings — nothing hardcoded here.
Structured output: request json_schema first, degrade to json_object + local
parse on 4xx rejection (capability degrade, docs/04 §4), then one repair
round-trip on parse failure (docs/03 §6).
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel

from sidecar.providers.errors import ProviderError
from sidecar.providers.retry import RetryPolicy, with_retries

_TIMEOUT = httpx.Timeout(120.0)


class ChatResult(BaseModel):
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class JsonOutput(BaseModel):
    """Structured output + usage from request_json."""

    data: dict
    input_tokens: int | None = None
    output_tokens: int | None = None


class ChatProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: httpx.Timeout = _TIMEOUT,
        code_prefix: str = "CHAT",
        transport: httpx.BaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.code = code_prefix
        self.retry_policy = retry_policy
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def chat(
        self,
        messages: list[dict],
        *,
        response_json_schema: dict | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        """One chat completion call, with HTTP-level retry (D53)."""
        body: dict[str, Any] = {"model": self.model, "messages": messages}
        if response_json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": response_json_schema, "strict": True},
            }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        try:
            return with_retries(
                lambda: self._request(body), policy=self.retry_policy
            )
        except ProviderError as e:
            if response_json_schema is not None and self._schema_rejected(e):
                return self._chat_with_json_object(messages, response_json_schema, max_tokens)
            raise

    def request_json(
        self, messages: list[dict], schema: dict, *, max_tokens: int | None = None
    ) -> JsonOutput:
        """Structured output with one repair round-trip on bad JSON."""
        first = self.chat(messages, response_json_schema=schema, max_tokens=max_tokens)
        try:
            data = parse_json_content(first.content)
            return JsonOutput(data=data, input_tokens=first.input_tokens,
                              output_tokens=first.output_tokens)
        except ValueError as e:
            repair = messages + [
                {"role": "assistant", "content": first.content},
                {
                    "role": "user",
                    "content": (
                        f"Your previous reply was not valid JSON ({e}). "
                        "Reply again with a single JSON object matching the schema exactly."
                    ),
                },
            ]
            second = self.chat(repair, response_json_schema=schema, max_tokens=max_tokens)
            try:
                data = parse_json_content(second.content)
                total_in = (first.input_tokens or 0) + (second.input_tokens or 0) or None
                total_out = (first.output_tokens or 0) + (second.output_tokens or 0) or None
                return JsonOutput(data=data, input_tokens=total_in, output_tokens=total_out)
            except ValueError as e2:
                raise ProviderError.terminal(
                    self.code, "BADJSON", f"model failed to produce valid JSON twice: {e2}"
                ) from e2

    def _chat_with_json_object(
        self, messages: list[dict], schema: dict, max_tokens: int | None
    ) -> ChatResult:
        """Capability degrade path (docs/04 §4): provider rejects json_schema."""
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages
            + [
                {
                    "role": "system",
                    "content": (
                        "Respond ONLY with a JSON object matching this schema:\n"
                        + json.dumps(schema, ensure_ascii=False)
                    ),
                }
            ],
            "response_format": {"type": "json_object"},
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        return with_retries(lambda: self._request(body), policy=self.retry_policy)

    def _request(self, body: dict) -> ChatResult:
        try:
            response = self._client.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.RequestError as e:
            raise ProviderError.network(self.code, f"{type(e).__name__}: {e}") from e
        if response.status_code != 200:
            raise ProviderError.from_status(self.code, response.status_code, response.text)
        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise ProviderError.terminal(
                self.code, "PARSE", f"unexpected chat response shape: {e}"
            ) from e
        usage = data.get("usage") or {}
        return ChatResult(
            content=content,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )

    def _schema_rejected(self, e: ProviderError) -> bool:
        return e.code.endswith("-400") and "response_format" in str(e)

    def close(self) -> None:
        self._client.close()


def parse_json_content(content: str) -> dict:
    """Parse model JSON output; tolerate code fences. Raises ValueError."""
    text = content.strip()
    if text.startswith("```"):
        text = text.removeprefix("```json").removeprefix("```")
        text = text.removesuffix("```").strip()
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("top-level JSON is not an object")
    return parsed
