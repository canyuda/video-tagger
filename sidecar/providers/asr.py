"""DashScope paraformer-v2 ASR provider (docs/03 §3.1).

Local audio goes through Bailian temporary storage: get upload policy ->
multipart POST to OSS -> `oss://` URL (48h) -> async submit with
X-DashScope-OssResourceResolve -> poll task -> download sentence-level result.
Only used for classification L2 audio samples in V1 (3x10s excerpts).
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
from pydantic import BaseModel

from sidecar.providers.errors import ProviderError
from sidecar.providers.retry import RetryPolicy, with_retries

_TIMEOUT = httpx.Timeout(60.0)
ASR_RESOLVE_HEADER = {"X-DashScope-OssResourceResolve": "enable"}


class Sentence(BaseModel):
    text: str
    begin_ms: int
    end_ms: int


class AsrProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str = "paraformer-v2",
        poll_interval: float = 3.0,
        poll_timeout: float = 180.0,
        transport: httpx.BaseTransport | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.poll_interval = poll_interval
        self.poll_timeout = poll_timeout
        self.retry_policy = retry_policy
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {api_key}"}, timeout=_TIMEOUT, transport=transport
        )

    def transcribe(self, audio_path: Path) -> list[Sentence]:
        """Full flow: upload -> submit -> poll -> download -> sentences."""
        file_url = self._upload(audio_path)
        task_id = self._submit(file_url)
        result_url = self._await_task(task_id)
        return self._download_sentences(result_url)

    # -- upload ---------------------------------------------------------

    def _upload(self, audio_path: Path) -> str:
        policy = with_retries(
            lambda: self._get_policy(audio_path.name), policy=self.retry_policy
        )
        with_retries(lambda: self._post_file(policy, audio_path), policy=self.retry_policy)
        return "oss://" + policy["key"]

    def _get_policy(self, filename: str) -> dict:
        try:
            r = self._client.get(
                f"{self.base_url}/api/v1/uploads",
                params={"action": "getPolicy", "model": self.model},
            )
        except httpx.RequestError as e:
            raise ProviderError.network("ASR", f"get_policy: {type(e).__name__}") from e
        if r.status_code != 200:
            raise ProviderError.from_status("ASR", r.status_code, r.text)
        data = (r.json() or {}).get("data") or {}
        required = ("upload_host", "policy", "signature", "upload_dir", "oss_access_key_id")
        if any(k not in data for k in required):
            missing = sorted(set(required) - set(data))
            raise ProviderError.terminal("ASR", "PARSE", f"policy response missing: {missing}")
        data["key"] = f"{data['upload_dir'].rstrip('/')}/{filename}"
        return data

    def _post_file(self, policy: dict, audio_path: Path) -> None:
        fields = {
            "OSSAccessKeyId": policy["oss_access_key_id"],
            "Signature": policy["signature"],
            "policy": policy["policy"],
            "key": policy["key"],
            "x-oss-object-acl": policy.get("x_oss_object_acl", "private"),
            "x-oss-forbid-overwrite": policy.get("x_oss_forbid_overwrite", "true"),
            "success_action_status": "200",
        }
        try:
            with audio_path.open("rb") as f:
                r = self._client.post(
                    policy["upload_host"],
                    data=fields,
                    files={"file": (audio_path.name, f, "audio/mpeg")},
                )
        except httpx.RequestError as e:
            raise ProviderError.network("ASR", f"upload: {type(e).__name__}") from e
        if r.status_code not in (200, 204):
            raise ProviderError.from_status("ASR", r.status_code, r.text)

    # -- async task -----------------------------------------------------

    def _submit(self, file_url: str) -> str:
        body = {"model": self.model, "input": {"file_urls": [file_url]}}

        def call() -> str:
            try:
                r = self._client.post(
                    f"{self.base_url}/api/v1/services/audio/asr/transcription",
                    json=body,
                    headers={"X-DashScope-Async": "enable", **ASR_RESOLVE_HEADER},
                )
            except httpx.RequestError as e:
                raise ProviderError.network("ASR", f"submit: {type(e).__name__}") from e
            if r.status_code != 200:
                raise ProviderError.from_status("ASR", r.status_code, r.text)
            output = (r.json() or {}).get("output") or {}
            task_id = output.get("task_id")
            if not task_id:
                raise ProviderError.terminal("ASR", "PARSE", "submit response missing task_id")
            return task_id

        return with_retries(call, policy=self.retry_policy)

    def _await_task(self, task_id: str) -> str:
        deadline = time.monotonic() + self.poll_timeout
        while True:
            status, result = self._poll(task_id)
            if status == "SUCCEEDED":
                return result
            if status in ("FAILED", "CANCELED"):
                raise ProviderError.terminal("ASR", "TASK", f"task ended as {status}")
            if time.monotonic() > deadline:
                raise ProviderError(
                    "ASR-TIMEOUT", f"poll exceeded {self.poll_timeout}s", retryable=True
                )
            time.sleep(self.poll_interval)

    def _poll(self, task_id: str) -> tuple[str, str]:
        def call() -> tuple[str, str]:
            try:
                r = self._client.post(f"{self.base_url}/api/v1/tasks/{task_id}")
            except httpx.RequestError as e:
                raise ProviderError.network("ASR", f"poll: {type(e).__name__}") from e
            if r.status_code != 200:
                raise ProviderError.from_status("ASR", r.status_code, r.text)
            output = (r.json() or {}).get("output") or {}
            status = output.get("task_status", "")
            if status != "SUCCEEDED":
                return status, ""
            results = output.get("results") or []
            if not results:
                raise ProviderError.terminal("ASR", "PARSE", "SUCCEEDED but no results[]")
            first = results[0]
            if first.get("subtask_status") not in (None, "SUCCEEDED"):
                raise ProviderError.terminal(
                    "ASR", "SUBTASK", f"subtask {first.get('subtask_status')}: "
                    f"{first.get('code', '')} {first.get('message', '')}"
                )
            url = first.get("transcription_url")
            if not url:
                raise ProviderError.terminal("ASR", "PARSE", "result missing transcription_url")
            return "SUCCEEDED", url

        return with_retries(call, policy=self.retry_policy)

    def _download_sentences(self, result_url: str) -> list[Sentence]:
        def call() -> list[Sentence]:
            try:
                r = self._client.get(result_url)
            except httpx.RequestError as e:
                raise ProviderError.network("ASR", f"download: {type(e).__name__}") from e
            if r.status_code != 200:
                raise ProviderError.from_status("ASR", r.status_code, r.text)
            return parse_transcription(r.json())

        return with_retries(call, policy=self.retry_policy)

    def close(self) -> None:
        self._client.close()


def parse_transcription(data: dict) -> list[Sentence]:
    """transcripts[].sentences[] -> Sentence list. Empty audio -> []."""
    try:
        transcripts = data["transcripts"] or []
        sentences = transcripts[0].get("sentences", []) if transcripts else []
    except (KeyError, TypeError, IndexError) as e:
        raise ProviderError.terminal("ASR", "PARSE", f"unexpected transcription shape: {e}") from e
    return [
        Sentence(
            text=s.get("text", ""),
            begin_ms=int(s.get("begin_time", 0)),
            end_ms=int(s.get("end_time", 0)),
        )
        for s in sentences
    ]
