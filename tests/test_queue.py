"""TaskWorker tests with a fake classifier (real ffmpeg file, no API calls)."""

import shutil
import subprocess
import time

import pytest

from sidecar.classify.pipeline import ClassificationResult
from sidecar.queue import EventBus, TaskWorker
from sidecar.store import Store


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture()
def video(tmp_path):
    if not _ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe not available")
    path = tmp_path / "v.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=5:size=320x240:rate=10",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=5",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
         str(path)],
        check=True,
    )
    return path


def make_result(file_name: str, type_: str = "monologue", confidence: float = 0.9):
    return ClassificationResult(
        file=file_name, file_hash="h" * 64, taxonomy_version=1,
        type=type_, type_name="口播", probabilities={type_: confidence},
        confidence=confidence, analysis_worthy=True, summary_zh="测试",
        observations=["person-facing-camera"], free_note=None, other_guess=None,
        l2_triggered=False, l2_transcript_excerpt=None, decision_layer="jev",
        degraded=False, speech_driven=0.9, timings_s={}, usage_tokens={},
        models={"vlm": "test", "decision": "test", "asr": None},
    )


class FakeClassifier:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error

    def classify(self, path, *, fast_mode=False):
        if self.error:
            raise self.error
        return self.result


def run_worker(store, bus, classifier, video_path, **kw) -> dict:
    worker = TaskWorker(store, lambda: classifier, bus, poll_interval=0.05, **kw)
    task = store.create_task(source_type="local", file_path=str(video_path))
    worker.start(concurrency=1)
    deadline = time.time() + 15
    while time.time() < deadline:
        current = store.get_task(task["id"])
        if current["status"] in ("classified", "failed", "rejected", "cancelled"):
            break
        time.sleep(0.05)
    worker.stop()
    return store.get_task(task["id"])


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg not available")
class TestWorkerHappyPath:
    def test_created_to_classified(self, video, tmp_path):
        store = Store(tmp_path / "t.db")
        store.init()
        bus = EventBus()
        classifier = FakeClassifier(make_result(video.name))
        final = run_worker(store, bus, classifier, video)
        assert final["status"] == "classified"
        assert final["result_type"] == "monologue"
        assert final["file_hash"] and final["source_meta"]["duration"] > 4

        artifact = store.get_artifact(final["id"], "classify", 1)
        assert artifact["type"] == "monologue"

        sub, replay = bus.subscribe(final["id"])
        bus.unsubscribe(final["id"], sub)
        stages = [r["data"]["stage"] for r in replay if r["event"] == "stage_changed"]
        assert stages == ["probing", "classifying"]
        assert any(r["event"] == "done" for r in replay)

    def test_missing_file_rejected(self, tmp_path):
        store = Store(tmp_path / "t.db")
        store.init()
        bus = EventBus()
        final = run_worker(store, bus, FakeClassifier(make_result("x")), tmp_path / "nope.mp4")
        assert final["status"] == "rejected"
        assert "不存在" in final["error_msg"]

    def test_duplicate_import_rejected(self, video, tmp_path):
        from sidecar.hashing import sha256_file

        store = Store(tmp_path / "t.db")
        store.init()
        first = store.create_task(source_type="local", file_path=str(video))
        store.update_task(first["id"], file_hash=sha256_file(video), status="classified")
        final = run_worker(store, EventBus(), FakeClassifier(make_result(video.name)), video)
        assert final["status"] == "rejected"
        assert "重复" in final["error_msg"]

    def test_corrupt_file_rejected(self, tmp_path):
        bad = tmp_path / "bad.mp4"
        bad.write_bytes(b"garbage")
        store = Store(tmp_path / "t.db")
        store.init()
        final = run_worker(store, EventBus(), FakeClassifier(make_result("bad")), bad)
        assert final["status"] == "rejected"

    def test_provider_error_marks_failed_retryable(self, video, tmp_path):
        from sidecar.providers.errors import ProviderError

        store = Store(tmp_path / "t.db")
        store.init()
        error = ProviderError("CHAT-429", "rate limited", retryable=True)
        final = run_worker(store, EventBus(), FakeClassifier(error=error), video)
        assert final["status"] == "failed"
        assert final["error_stage"] == "classifying"

    def test_cancelled_before_run_stays_cancelled(self, video, tmp_path):
        store = Store(tmp_path / "t.db")
        store.init()
        task = store.create_task(source_type="local", file_path=str(video))
        store.update_task(task["id"], status="cancelled", stage="cancelled")
        # Worker finds nothing to claim (status != created).
        worker = TaskWorker(store, lambda: FakeClassifier(make_result(video.name)),
                            EventBus(), poll_interval=0.05)
        worker.start(concurrency=1)
        time.sleep(0.3)
        worker.stop()
        assert store.get_task(task["id"])["status"] == "cancelled"


class FakeDouyinClient:
    """Stand-in DouyinClient: no network, writes a real tiny mp4."""

    def __init__(self, video_path, *, fail=False):
        self.video_path = video_path
        self.fail = fail

    def fetch_aweme_detail(self, aweme_id):
        if self.fail:
            from sidecar.download.api import DouyinApiError
            raise DouyinApiError("HTTP 403; 风控")
        return {
            "desc": "测试视频标题",
            "author": {"nickname": "作者"},
            "video": {"duration": 5000, "play_addr": {"url_list": ["https://cdn/x.mp4"]}},
            "text_extra": [{"hashtag_name": "测试"}],
        }

    def download_video(self, url, file_path):
        import shutil

        shutil.copyfile(self.video_path, file_path)


class TestDouyinStage:
    def test_douyin_download_then_classified(self, video, tmp_path):
        store = Store(tmp_path / "t.db")
        store.init()
        classifier = FakeClassifier(make_result(video.name))
        worker = TaskWorker(
            store, lambda: classifier, EventBus(), poll_interval=0.05,
            work_root=tmp_path / "work",
            douyin_factory=lambda: FakeDouyinClient(video),
        )
        task = store.create_task(
            source_type="douyin", file_path="",
            source_url="https://www.douyin.com/video/7350000000000000001",
        )
        worker.start(concurrency=1)
        deadline = time.time() + 15
        while time.time() < deadline:
            current = store.get_task(task["id"])
            if current["status"] in ("classified", "failed", "rejected"):
                break
            time.sleep(0.05)
        worker.stop()
        final = store.get_task(task["id"])
        assert final["status"] == "classified", final["error_msg"]
        assert final["source_meta"]["aweme_id"] == "7350000000000000001"
        assert final["source_meta"]["author"] == "作者"
        assert final["file_path"].endswith(".mp4")

    def test_douyin_adapter_failure_is_stage_retryable(self, video, tmp_path):
        store = Store(tmp_path / "t.db")
        store.init()
        worker = TaskWorker(
            store, lambda: FakeClassifier(make_result(video.name)), EventBus(),
            poll_interval=0.05, work_root=tmp_path / "work",
            douyin_factory=lambda: FakeDouyinClient(video, fail=True),
        )
        task = store.create_task(
            source_type="douyin", file_path="",
            source_url="https://www.douyin.com/video/7350000000000000002",
        )
        worker.start(concurrency=1)
        deadline = time.time() + 15
        while time.time() < deadline:
            current = store.get_task(task["id"])
            if current["status"] in ("classified", "failed", "rejected"):
                break
            time.sleep(0.05)
        worker.stop()
        final = store.get_task(task["id"])
        assert final["status"] == "failed"
        assert final["error_stage"] == "downloading"

    def test_douyin_without_factory_rejected(self, video, tmp_path):
        store = Store(tmp_path / "t.db")
        store.init()
        worker = TaskWorker(
            store, lambda: FakeClassifier(make_result(video.name)), EventBus(),
            poll_interval=0.05, work_root=tmp_path / "work", douyin_factory=None,
        )
        task = store.create_task(
            source_type="douyin", file_path="", source_url="https://v.douyin.com/x/",
        )
        worker.start(concurrency=1)
        deadline = time.time() + 15
        while time.time() < deadline:
            current = store.get_task(task["id"])
            if current["status"] in ("classified", "failed", "rejected"):
                break
            time.sleep(0.05)
        worker.stop()
        final = store.get_task(task["id"])
        assert final["status"] == "rejected"
        assert "Cookie" in final["error_msg"]


class TestStartupRecovery:
    def test_requeue_inflight_resets_only_in_flight(self, tmp_path):
        store = Store(tmp_path / "t.db")
        store.init()
        states = {
            "a": "classifying", "b": "probing", "c": "downloading",
            "d": "transcribing", "e": "analyzing",
            "f": "classified", "g": "failed", "h": "cancelled", "i": "rejected",
            "j": "created",
        }
        ids = {}
        for name, status in states.items():
            ids[name] = store.create_task(source_type="local", file_path=f"/{name}.mp4")["id"]
            stage = status if status in ("created",) else status
            store.update_task(ids[name], status=status, stage=stage)
        requeued = store.requeue_inflight()
        assert requeued == 5  # a-e only
        for name, status in states.items():
            current = store.get_task(ids[name])
            if name in ("a", "b", "c", "d", "e"):
                assert current["status"] == "created", name
            else:
                assert current["status"] == status, name
