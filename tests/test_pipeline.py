"""End-to-end pipeline tests with mocked providers and a real generated video.

Covers: happy path, caching (perceive + final result), L2 trigger on low
confidence, degrade-to-VLM-direct on Jev failure, audio-only rejection.
"""

import json
import shutil
import subprocess

import httpx
import pytest

from sidecar.cache import FileCache
from sidecar.classify.pipeline import ClassificationError, Classifier
from sidecar.config import Settings
from sidecar.providers.asr import AsrProvider
from sidecar.providers.chat import ChatProvider
from sidecar.providers.jev import JevClient
from sidecar.taxonomy import builtin_taxonomy


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    if not _ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe not available")
    path = tmp_path_factory.mktemp("pipe") / "v.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=8:size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
    )
    return path


def settings() -> Settings:
    return Settings(
        dashscope_api_key="sk", typesafe_api_key="ts",
        confidence_threshold=0.85, frame_count=4, frame_max_edge=256,
    )


def chat_ok_body(content: str) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"content": content}}],
         "usage": {"prompt_tokens": 100, "completion_tokens": 20}}
    ).encode()


PERCEPTION_OUTPUT = json.dumps(
    {
        "tags": ["person-facing-camera", "subtitle-bar-embedded"],
        "free_note": None,
        "summary_zh": "人物面对镜头",
        "other_guess": None,
    },
    ensure_ascii=False,
)

L2_OUTPUT = json.dumps(
    {"tags": ["conversational-speech"], "free_note": None,
     "summary_zh": "自然语言口播", "other_guess": None},
    ensure_ascii=False,
)

JEV_HIGH_CONF = {
    "model": "jev-test",
    "answers": {
        "video_type": {"type": "choice", "choice": "monologue",
                       "confidence": 0.93,
                       "probabilities": {"monologue": 0.93, "other": 0.07}},
        "speech_or_text_driven": {"type": "noul", "noul": 0.9},
    },
    "usage": {"input_tokens": 300, "output_tokens": 30},
}

JEV_LOW_CONF = {
    "model": "jev-test",
    "answers": {
        "video_type": {"type": "choice", "choice": "singing",
                       "confidence": 0.6,
                       "probabilities": {"singing": 0.6, "monologue": 0.4}},
        "speech_or_text_driven": {"type": "noul", "noul": 0.4},
    },
    "usage": {"input_tokens": 300, "output_tokens": 30},
}

TRANSCRIPTION = {"transcripts": [{"sentences": [
    {"text": "今天聊聊怎么涨粉", "begin_time": 0, "end_time": 3000},
]}]}


def make_classifier(
    tmp_path, *, chat_handler, jev_handler, with_asr: bool = False, asr_handler=None
) -> tuple[Classifier, dict]:
    calls = {"chat": 0, "jev": 0, "asr": 0}

    def chat_transport(request: httpx.Request) -> httpx.Response:
        calls["chat"] += 1
        return chat_handler(request, calls["chat"])

    def jev_transport(request: httpx.Request) -> httpx.Response:
        calls["jev"] += 1
        return jev_handler(request, calls["jev"])

    chat = ChatProvider(base_url="https://c.example/v1", api_key="k",
                        model="test-vlm", transport=httpx.MockTransport(chat_transport))
    jev = JevClient(base_url="https://j.example", api_key="k", model="jev-test",
                    transport=httpx.MockTransport(jev_transport))
    asr = None
    if with_asr:
        def asr_transport(request: httpx.Request) -> httpx.Response:
            calls["asr"] += 1
            url = str(request.url)
            if "action=getPolicy" in url:
                return httpx.Response(200, json={"data": {
                    "upload_host": "https://oss.example.com", "policy": "p",
                    "signature": "s", "upload_dir": "d",
                    "oss_access_key_id": "AK", "x_oss_object_acl": "private",
                    "x_oss_forbid_overwrite": "true"}})
            if url.startswith("https://oss.example.com"):
                return httpx.Response(200)
            if url.endswith("/transcription"):
                return httpx.Response(200, json={"output": {"task_id": "T"}})
            if "/tasks/" in url:
                return httpx.Response(200, json={"output": {
                    "task_status": "SUCCEEDED",
                    "results": [{"subtask_status": "SUCCEEDED",
                                 "transcription_url": "https://r.example/x.json"}]}})
            return httpx.Response(200, json=TRANSCRIPTION)

        asr = AsrProvider(base_url="https://a.example", api_key="k",
                          transport=httpx.MockTransport(asr_transport))

    classifier = Classifier(
        settings=settings(), taxonomy=builtin_taxonomy(), chat=chat, jev=jev, asr=asr,
        cache=FileCache(tmp_path / "cache"), work_root=tmp_path / "work",
    )
    return classifier, calls


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg/ffprobe not available")
class TestPipeline:
    def test_happy_path_jev_decision(self, video, tmp_path):
        def chat_handler(request, n):
            return httpx.Response(200, content=chat_ok_body(PERCEPTION_OUTPUT))

        def jev_handler(request, n):
            return httpx.Response(200, json=JEV_HIGH_CONF)

        c, calls = make_classifier(tmp_path, chat_handler=chat_handler, jev_handler=jev_handler)
        result = c.classify(video)
        assert result.type == "monologue"
        assert result.type_name == "口播"
        assert result.analysis_worthy is True
        assert result.decision_layer == "jev" and not result.degraded
        assert result.confidence == pytest.approx(0.93)
        assert result.speech_driven == pytest.approx(0.9)
        assert not result.l2_triggered
        assert result.observations == ["person-facing-camera", "subtitle-bar-embedded"]
        assert calls["chat"] == 1 and calls["jev"] == 1

    def test_full_result_cached_second_run_no_calls(self, video, tmp_path):
        calls_seen = {}

        def chat_handler(request, n):
            calls_seen["chat"] = n
            return httpx.Response(200, content=chat_ok_body(PERCEPTION_OUTPUT))

        def jev_handler(request, n):
            calls_seen["jev"] = n
            return httpx.Response(200, json=JEV_HIGH_CONF)

        c, calls = make_classifier(tmp_path, chat_handler=chat_handler, jev_handler=jev_handler)
        first = c.classify(video)
        second = c.classify(video)
        assert second == first
        assert calls["chat"] == 1 and calls["jev"] == 1  # second run was cache-hit

    def test_low_confidence_triggers_l2_and_redecides(self, video, tmp_path):
        def chat_handler(request, n):
            # 1: perception, 2: L2 audio observation
            content = PERCEPTION_OUTPUT if n == 1 else L2_OUTPUT
            return httpx.Response(200, content=chat_ok_body(content))

        def jev_handler(request, n):
            return httpx.Response(200, json=JEV_LOW_CONF if n == 1 else JEV_HIGH_CONF)

        c, calls = make_classifier(
            tmp_path, chat_handler=chat_handler, jev_handler=jev_handler,
            with_asr=True,
        )
        result = c.classify(video)
        assert result.l2_triggered
        assert result.l2_transcript_excerpt == "今天聊聊怎么涨粉"
        assert "conversational-speech" in result.observations
        assert result.type == "monologue"  # L2 re-decision wins
        assert calls["asr"] == 5  # policy, upload, submit, poll, download
        assert calls["jev"] == 2

    def test_jev_failure_degrades_to_vlm_direct(self, video, tmp_path):
        vlm_direct = json.dumps({"type": "tutorial", "confidence": 0.8})

        def chat_handler(request, n):
            content = PERCEPTION_OUTPUT if n == 1 else vlm_direct
            return httpx.Response(200, content=chat_ok_body(content))

        def jev_handler(request, n):
            return httpx.Response(500, text="jev down")

        c, _ = make_classifier(tmp_path, chat_handler=chat_handler, jev_handler=jev_handler)
        result = c.classify(video)
        assert result.degraded and result.decision_layer == "vlm-direct"
        assert result.type == "tutorial"
        assert any("jev failed" in note for note in result.notes)

    def test_audio_only_file_rejected_terminal(self, tmp_path):
        # A real audio-only file: probe succeeds but has no video stream.
        if not _ffmpeg_available():
            pytest.skip("ffmpeg not available")
        audio = tmp_path / "audio_only.mp3"
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
             "-i", "sine=frequency=440:duration=2", str(audio)],
            check=True,
        )

        def chat_handler(request, n):
            return httpx.Response(200, content=chat_ok_body(PERCEPTION_OUTPUT))

        def jev_handler(request, n):
            return httpx.Response(200, json=JEV_HIGH_CONF)

        c, _ = make_classifier(tmp_path, chat_handler=chat_handler, jev_handler=jev_handler)
        with pytest.raises(ClassificationError, match="no video stream") as ei:
            c.classify(audio)
        assert ei.value.terminal

    def test_ffprobe_failure_is_terminal(self, tmp_path):
        fake = tmp_path / "garbage.mp4"
        fake.write_bytes(b"definitely not a video")
        def chat_handler(request, n):
            return httpx.Response(200, content=chat_ok_body(PERCEPTION_OUTPUT))

        def jev_handler(request, n):
            return httpx.Response(200, json=JEV_HIGH_CONF)

        c, _ = make_classifier(tmp_path, chat_handler=chat_handler, jev_handler=jev_handler)
        with pytest.raises(ClassificationError) as ei:
            c.classify(fake)
        assert ei.value.terminal
