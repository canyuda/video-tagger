"""TypeSafe Jev decision provider (docs/03 §3.0, docs/04 §4).

POST {base_url}/v1/systemone with {state, model, questions}; answers come
back typed per question (Choice: choice + probabilities + confidence).
Only English state/criteria ever goes here (D07).
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel

from sidecar.providers.errors import ProviderError
from sidecar.providers.retry import RetryPolicy, with_retries

_TIMEOUT = httpx.Timeout(60.0)


class ChoiceAnswer(BaseModel):
    choice: str
    probabilities: dict[str, float]
    confidence: float


class JevResult(BaseModel):
    model: str
    answers: dict[str, Any]
    input_tokens: int | None = None
    output_tokens: int | None = None

    def choice(self, question_id: str) -> ChoiceAnswer:
        answer = self.answers.get(question_id)
        if answer is None:
            raise ProviderError.terminal(
                "DEC", "PARSE", f"response missing answer for question {question_id!r}"
            )
        try:
            return ChoiceAnswer.model_validate(answer)
        except Exception as e:  # noqa: BLE001
            raise ProviderError.terminal(
                "DEC", "PARSE", f"answer for {question_id!r} is not a Choice: {e}"
            ) from e


class JevClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: httpx.Timeout = _TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.retry_policy = retry_policy
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
            transport=transport,
        )

    def ask(self, state: dict | str, questions: dict[str, dict]) -> JevResult:
        body = {"state": state, "model": self.model, "questions": questions}
        return with_retries(lambda: self._request(body), policy=self.retry_policy)

    def _request(self, body: dict) -> JevResult:
        try:
            response = self._client.post(f"{self.base_url}/v1/systemone", json=body)
        except httpx.RequestError as e:
            raise ProviderError.network("DEC", f"{type(e).__name__}: {e}") from e
        if response.status_code != 200:
            raise ProviderError.from_status("DEC", response.status_code, response.text)
        try:
            data = response.json()
            return JevResult(
                model=data["model"],
                answers=data["answers"],
                input_tokens=data.get("usage", {}).get("input_tokens"),
                output_tokens=data.get("usage", {}).get("output_tokens"),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise ProviderError.terminal("DEC", "PARSE", f"unexpected Jev response: {e}") from e

    def close(self) -> None:
        self._client.close()
