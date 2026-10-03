"""Task queue: N worker threads driving the task state machine (docs/04 §6).

V1 local flow: created -> probing (hash/dedupe/ffprobe boundary) ->
classifying (perception + decision + L2) -> classified. Failures map to:
- rejected: terminal file problems (corrupt, audio-only, duplicate import)
- failed: retryable provider/network errors (retry endpoint resets the stage)
- cancelled: user terminal (in-flight classification result is discarded)

Events go to an in-memory EventBus with per-task replay so SSE subscribers
never miss a stage change.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from queue import SimpleQueue

from sidecar.classify.pipeline import (
    ClassificationError,
    ClassificationResult,
    Classifier,
)
from sidecar.hashing import sha256_file
from sidecar.media import MediaError, probe
from sidecar.providers.errors import ProviderError
from sidecar.store import Store, row_to_task, utc_now

ClassifierFactory = Callable[[], Classifier]


class EventBus:
    """Per-task fan-out with bounded replay (late SSE subscribers catch up)."""

    REPLAY_LIMIT = 200

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._history: dict[str, list[dict]] = {}
        self._subscribers: dict[str, list[SimpleQueue]] = {}

    def publish(self, task_id: str, event: str, data: dict) -> None:
        record = {"event": event, "data": data}
        with self._lock:
            history = self._history.setdefault(task_id, [])
            history.append(record)
            del history[: max(0, len(history) - self.REPLAY_LIMIT)]
            subscribers = list(self._subscribers.get(task_id, []))
        for sub in subscribers:
            sub.put(record)

    def subscribe(self, task_id: str) -> tuple[SimpleQueue, list[dict]]:
        """Register a subscriber and return (live queue, replayed events)."""
        sub: SimpleQueue = SimpleQueue()
        with self._lock:
            replay = list(self._history.get(task_id, []))
            self._subscribers.setdefault(task_id, []).append(sub)
        return sub, replay

    def unsubscribe(self, task_id: str, sub: SimpleQueue) -> None:
        with self._lock:
            subs = self._subscribers.get(task_id, [])
            if sub in subs:
                subs.remove(sub)


class TaskWorker:
    """Pulls pending tasks from the store and runs the classification flow."""

    def __init__(
        self,
        store: Store,
        classifier_factory: ClassifierFactory,
        bus: EventBus,
        *,
        concurrency: int = 3,
        poll_interval: float = 0.5,
        work_root: Path | None = None,
        douyin_factory: Callable[[], object] | None = None,
        analyzer_factory: Callable[..., object] | None = None,
    ) -> None:
        self.store = store
        self.factory = classifier_factory
        self.bus = bus
        self.poll_interval = poll_interval
        self.work_root = work_root
        self.douyin_factory = douyin_factory
        self.analyzer_factory = analyzer_factory or _default_analyzer_factory
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self, concurrency: int | None = None) -> None:
        n = concurrency or 3  # D27: queue concurrency 3-5
        for i in range(n):
            t = threading.Thread(target=self._loop, name=f"task-worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=timeout)

    # -- main loop --------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            task = self._claim()
            if task is None:
                self._stop.wait(self.poll_interval)
                continue
            try:
                self._process(task)
            except Exception as e:  # noqa: BLE001 — worker must never die
                current = self.store.get_task(task["id"])
                stage = current["stage"] if current else task.get("stage", "classifying")
                self._finish_error(task["id"], stage, str(e), retryable=True)

    def _claim(self) -> dict | None:
        """Atomically claim one runnable task (created -> probing, or resume
        an analysis request at extracting)."""
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM tasks WHERE status IN ('created', 'extracting')"
                " ORDER BY created_at LIMIT 1"
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            next_status = "probing" if row["status"] == "created" else "extracting"
            conn.execute(
                "UPDATE tasks SET status = ?, stage = ?,"
                " updated_at = ? WHERE id = ? AND status = ?",
                (next_status, next_status, utc_now(), row["id"], row["status"]),
            )
            conn.execute("COMMIT")
        task = row_to_task(row)  # parse source_meta JSON (raw row would break spread)
        task["status"] = next_status
        task["stage"] = next_status
        return task

    def _cancelled(self, task_id: str) -> bool:
        task = self.store.get_task(task_id)
        return task is None or task["status"] == "cancelled"

    # -- stages -------------------------------------------------------------

    def _process(self, task: dict) -> None:
        task_id = task["id"]

        # V2 analysis flow (docs/03 §2): extracting -> transcribing ->
        # sampling -> analyzing -> done.
        if task["status"] == "extracting":
            self._run_analysis(task)
            return

        # -- downloading (douyin source, docs/04 §5: adapter failures only
        #    stop this stage) ------------------------------------------------
        if task["source_type"] == "douyin":
            local_path = self._download_douyin(task)
            if local_path is None:
                return
            task = self.store.get_task(task_id)  # download stage wrote source_meta
            task["file_path"] = str(local_path)

        self.bus.publish(task_id, "stage_changed",
                         {"task_id": task_id, "stage": "probing", "at": utc_now()})
        path = Path(task["file_path"])

        if not path.is_file():
            self._finish_rejected(task_id, "probing", f"文件不存在: {path}")
            return

        file_hash = sha256_file(path)
        duplicate = self.store.find_by_hash(file_hash)
        if duplicate is not None and duplicate["id"] != task_id:
            self.store.update_task(task_id, file_hash=file_hash)
            self._finish_rejected(
                task_id, "probing",
                f"重复导入：与任务 {duplicate['id'][:8]} 的文件相同（已分类过则跳过）",
            )
            return

        try:
            info = probe(path)
        except MediaError as e:
            self._finish_rejected(task_id, "probing", f"文件损坏或无法解析: {e}")
            return
        if not info.has_video:
            self._finish_rejected(task_id, "probing", "仅支持视频文件（该文件没有视频流）")
            return

        self.store.update_task(
            task_id, file_hash=file_hash,
            source_meta={**(task.get("source_meta") or {}),
                         "duration": info.duration,
                         "has_audio": info.has_audio,
                         "width": info.width, "height": info.height},
        )
        if self._cancelled(task_id):
            return

        # -- classifying ---------------------------------------------------
        self.store.update_task(task_id, status="classifying", stage="classifying")
        self.bus.publish(task_id, "stage_changed",
                         {"task_id": task_id, "stage": "classifying", "at": utc_now()})
        self.bus.publish(task_id, "progress",
                         {"task_id": task_id, "stage": "classifying", "percent": 10.0,
                          "detail": "抽取样本帧并调用感知层"})
        try:
            classifier = self.factory()
            result: ClassificationResult = classifier.classify(path)
        except ClassificationError as e:
            if e.terminal:
                self._finish_rejected(task_id, "classifying", str(e))
            else:
                self._finish_error(task_id, "classifying", str(e), retryable=True)
            return
        except ProviderError as e:
            self._finish_error(task_id, "classifying", f"[{e.code}] {e}", retryable=e.retryable)
            return

        if self._cancelled(task_id):
            return  # user cancelled mid-flight: keep cancelled, drop result

        self.store.update_task(
            task_id,
            status="classified",
            stage="classified",
            result_type=result.type,
            confidence=result.confidence,
            error_stage=None,
            error_msg=None,
        )
        self.store.put_artifact(task_id, "classify", result.taxonomy_version,
                                result.model_dump())
        self.bus.publish(task_id, "progress",
                         {"task_id": task_id, "stage": "classifying", "percent": 100.0})
        self.bus.publish(task_id, "done", {"task_id": task_id, "status": "classified"})

    def _download_douyin(self, task: dict) -> Path | None:
        """Resolve + download one douyin link. Returns the local file path, or
        None after marking the task rejected/failed."""
        from sidecar.download.api import DouyinApiError, play_url, sanitize_title
        from sidecar.download.linkparser import LinkParseError, resolve_link

        task_id = task["id"]
        self.store.update_task(task_id, status="downloading", stage="downloading")
        self.bus.publish(task_id, "stage_changed",
                         {"task_id": task_id, "stage": "downloading", "at": utc_now()})

        if self.douyin_factory is None:
            self._finish_rejected(task_id, "downloading",
                                  "抖音功能需要配置 Cookie（设置 → 抖音 Cookie）")
            return None

        try:
            client = self.douyin_factory()
            link = resolve_link(task.get("source_url") or "")
            if link.type != "video":
                self._finish_rejected(
                    task_id, "downloading",
                    "V1 仅支持单视频链接（主页/合集批量下载后续开放）",
                )
                return None
            aweme_id = link.id
            existing = next(
                (t for t in self.store.list_tasks()
                 if t["id"] != task_id
                 and (t.get("source_meta") or {}).get("aweme_id") == aweme_id),
                None,
            )
            if existing is not None:
                self._finish_rejected(
                    task_id, "downloading",
                    f"重复导入：aweme_id 已有任务 {existing['id'][:8]}",
                )
                return None

            self.bus.publish(task_id, "progress",
                             {"task_id": task_id, "stage": "downloading", "percent": 30.0,
                              "detail": "获取视频详情"})
            detail = client.fetch_aweme_detail(aweme_id)
            author = ((detail.get("author") or {}).get("nickname")) or ""
            source_meta = {
                "aweme_id": aweme_id,
                "desc": detail.get("desc") or "",
                "author": author,
                "duration": float((detail.get("video") or {}).get("duration") or 0) / 1000,
                "hashtags": [t.get("hashtag_name") for t in (detail.get("text_extra") or [])
                             if t.get("hashtag_name")],
            }
            dest_dir = (self.work_root or Path.home() / ".cache") / "downloads"
            dest_dir.mkdir(parents=True, exist_ok=True)
            target = dest_dir / f"{sanitize_title(source_meta['desc']) or aweme_id}_{aweme_id}.mp4"

            self.bus.publish(task_id, "progress",
                             {"task_id": task_id, "stage": "downloading", "percent": 60.0,
                              "detail": "下载视频文件"})
            client.download_video(play_url(detail), target)
            self.store.update_task(task_id, file_path=str(target), source_meta=source_meta)
            self.bus.publish(task_id, "progress",
                             {"task_id": task_id, "stage": "downloading", "percent": 100.0})
            return target
        except LinkParseError as e:
            self._finish_rejected(task_id, "downloading", str(e))
            return None
        except DouyinApiError as e:
            # Adapter failures are stage-retryable (docs/04 §5): cookie
            # expiry / risk control / network all land here.
            self._finish_error(task_id, "downloading", str(e), retryable=True)
            return None

    # -- terminal helpers ----------------------------------------------------

    def _run_analysis(self, task: dict) -> None:
        """V2 analysis stages for one task, artifact-cached (docs/03 §2)."""
        from sidecar.analyzer.pipeline import (
            TRANSCRIBE_STAGE_VERSION,
            AnalysisError,
        )
        from sidecar.store import seed_templates

        task_id = task["id"]
        file_path = Path(task["file_path"] or "")
        if not file_path.is_file():
            self._finish_rejected(task_id, "extracting", "视频文件不存在（可能已被移动）")
            return

        # Gating (fail-open, docs/03 §3.0): only high-confidence unworthy
        # types stop before analysis; the request artifact may carry an
        # override from the user's 改判 button.
        request = self.store.get_artifact(task_id, "analysis_request", 1) or {}
        if not request.get("override"):
            classify = self.store.get_artifact(task_id, "classify",
                                               task.get("taxonomy_version") or 1)
            if classify and not classify.get("analysis_worthy", True) \
                    and (classify.get("confidence") or 0) >= 0.85:
                self.store.update_task(task_id, status="no_value", stage="classifying")
                self.bus.publish(task_id, "done", {"task_id": task_id, "status": "no_value"})
                return

        try:
            info = probe(file_path)
        except MediaError as e:
            self._finish_rejected(task_id, "extracting", f"文件损坏或无法解析: {e}")
            return

        request_model = (request or {}).get("model")
        analyzer = self.analyzer_factory(model=request_model) if request_model \
            else self.analyzer_factory()
        try:
            analyzer.check_duration(info.duration)
        except AnalysisError as e:
            self._finish_rejected(task_id, "extracting", str(e))
            return
        file_hash = task.get("file_hash") or sha256_file(file_path)

        template = (self.store.get_template(request.get("template_id") or "tpl-general")
                    or {"id": "tpl-general", "name": "通用总结", "user_layer": ""})
        seed_templates(self.store)

        try:
            # -- transcribing ------------------------------------------------
            self.store.update_task(task_id, status="transcribing", stage="transcribing")
            self.bus.publish(task_id, "stage_changed",
                             {"task_id": task_id, "stage": "transcribing", "at": utc_now()})
            sentences = self.store.get_artifact(task_id, "transcribe", TRANSCRIBE_STAGE_VERSION)
            if sentences is None:
                sentences = analyzer.transcribe(file_path, file_hash)
                self.store.put_artifact(task_id, "transcribe", TRANSCRIBE_STAGE_VERSION,
                                        sentences)
            if self._cancelled(task_id):
                return

            # -- keyframes (sampling) ----------------------------------------
            self.store.update_task(task_id, status="sampling", stage="sampling")
            self.bus.publish(task_id, "stage_changed",
                             {"task_id": task_id, "stage": "sampling", "at": utc_now()})
            cached_frames = self.store.get_artifact(task_id, "keyframes",
                                                    TRANSCRIBE_STAGE_VERSION)
            if cached_frames is not None:
                frames = [
                    (ts, self._frame_path(file_hash, ts))
                    for ts in cached_frames["timestamps"]
                    if self._frame_path(file_hash, ts).is_file()
                ]
            else:
                transcript_chars = sum(len(s.get("text", "")) for s in sentences)
                frames, _dense = analyzer.keyframes(file_path, file_hash, info.duration,
                                                    transcript_chars)
                self.store.put_artifact(
                    task_id, "keyframes", TRANSCRIBE_STAGE_VERSION,
                    {"timestamps": [round(ts, 3) for ts, _ in frames]},
                )
            if self._cancelled(task_id):
                return

            # -- analyzing -----------------------------------------------------
            self.store.update_task(task_id, status="analyzing", stage="analyzing")
            self.bus.publish(task_id, "stage_changed",
                             {"task_id": task_id, "stage": "analyzing", "at": utc_now()})
            output = analyzer.analyze(
                video=file_path,
                file_hash=file_hash,
                duration_s=info.duration,
                sentences=sentences,
                frames=frames,
                template_name=template["name"],
                template_instructions=template.get("user_layer", ""),
                user_instructions=request.get("instructions") or "",
                metadata=task.get("source_meta"),
            )
            report_id = self.store.put_report(
                task_id, template_id=template["id"], model=output.model,
                prompt_snapshot=template.get("user_layer", ""),
                report_json=output.report, markdown=output.markdown,
            )
            if self._cancelled(task_id):
                return
            self.store.update_task(task_id, status="done", stage="analyzing",
                                   error_stage=None, error_msg=None)
            self.bus.publish(task_id, "done", {"task_id": task_id, "status": "done",
                                               "report_id": report_id})
        except AnalysisError as e:
            if e.terminal:
                self._finish_rejected(task_id, "analyzing", str(e))
            else:
                self._finish_error(task_id, "analyzing", str(e), retryable=True)
        except ProviderError as e:
            self._finish_error(task_id, "analyzing", f"[{e.code}] {e}",
                               retryable=e.retryable)

    def _frame_path(self, file_hash: str, ts: float) -> Path:
        return (self.work_root or Path(".")) / file_hash[:16] / "keyframes" / \
            f"frame_{int(ts):04d}.jpg"

    def _finish_rejected(self, task_id: str, stage: str, message: str) -> None:
        self.store.update_task(task_id, status="rejected", stage=stage,
                               error_stage=stage, error_msg=message)
        self.bus.publish(task_id, "error",
                         {"task_id": task_id, "stage": stage, "message": message,
                          "retryable": False})
        self.bus.publish(task_id, "done", {"task_id": task_id, "status": "rejected"})

    def _finish_error(self, task_id: str, stage: str, message: str, *, retryable: bool) -> None:
        status = "failed" if retryable else "rejected"
        self.store.update_task(task_id, status=status, stage=stage,
                               error_stage=stage, error_msg=message)
        self.bus.publish(task_id, "error",
                         {"task_id": task_id, "stage": stage, "message": message,
                          "retryable": retryable})
        self.bus.publish(task_id, "done", {"task_id": task_id, "status": status})


def _default_analyzer_factory():
    """Fallback analyzer factory; the app injects a settings-aware one."""
    from sidecar.analyzer.pipeline import Analyzer
    from sidecar.classify.factory import DATA_ROOT
    from sidecar.config import load_settings
    from sidecar.providers.asr import AsrProvider
    from sidecar.providers.chat import ChatProvider

    settings = load_settings({})
    return Analyzer(
        chat=ChatProvider(base_url=settings.dashscope_base_url,
                          api_key=settings.dashscope_api_key,
                          model=settings.dashscope_vlm_model),
        asr=AsrProvider(base_url=settings.dashscope_native_base_url,
                        api_key=settings.dashscope_api_key),
        work_root=DATA_ROOT / "work",
        model=settings.dashscope_vlm_model,
    )


def drain(queue: SimpleQueue) -> list[dict]:
    """Test helper: empty a subscriber queue into a list."""
    items: list[dict] = []
    while True:
        try:
            items.append(queue.get_nowait())
        except Exception:  # noqa: BLE001 — empty queue ends the drain
            return items
