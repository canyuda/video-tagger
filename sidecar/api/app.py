"""FastAPI application: task CRUD, SSE events, import, CSV, taxonomy,
settings. Auth: every path except /health requires the X-Auth-Token header
matching the token generated at startup (D54)."""

from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from sidecar.config import ConfigError, Settings, load_settings
from sidecar.queue import EventBus, TaskWorker
from sidecar.store import Store
from sidecar.taxonomy import TaxonomyError, builtin_taxonomy, load_taxonomy

TAXONOMY_SETTING_KEY = "taxonomy"

# The webview origin is tauri://localhost (macOS) / http(s)://tauri.localhost
# (Windows); dev uses the vite origin. Without CORS the WKWebView blocks
# every fetch from the UI with "Load failed".
ALLOWED_ORIGINS = (
    "tauri://localhost",
    "https://tauri.localhost",
    "http://tauri.localhost",
    "http://localhost:5173",
)


def create_app(
    *,
    db_path: Path,
    work_root: Path | None = None,
    token: str | None = None,
    start_worker: bool = True,
    concurrency: int = 3,
    settings_env: dict[str, str] | None = None,
    settings_file: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="video-tagger-sidecar", docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(ALLOWED_ORIGINS),
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.token = token or secrets.token_urlsafe(32)
    app.state.bus = EventBus()
    app.state.store = Store(db_path)
    app.state.work_root = work_root
    app.state.settings_env = settings_env if settings_env is not None else dict(os.environ)
    app.state.settings_file = settings_file
    app.state.worker = None

    @app.on_event("startup")
    def _startup() -> None:
        app.state.store.init()
        _seed_taxonomy(app.state.store)
        from sidecar.store import seed_templates

        seed_templates(app.state.store)
        # Crash recovery (docs/04 §6: 杀进程重启续跑): tasks left mid-stage by
        # a previous process go back to the queue; finished stages are skipped
        # via the artifact cache.
        requeued = app.state.store.requeue_inflight()
        if requeued:
            print(f"requeued {requeued} in-flight task(s) from previous run", flush=True)
        if start_worker:
            app.state.worker = TaskWorker(
                app.state.store,
                lambda: _build_classifier(app),
                app.state.bus,
                concurrency=concurrency,
                work_root=work_root,
                douyin_factory=lambda: _build_douyin_client(app),
                analyzer_factory=lambda: _build_analyzer(app),
            )
            app.state.worker.start(concurrency)

    @app.on_event("shutdown")
    def _shutdown() -> None:
        if app.state.worker is not None:
            app.state.worker.stop()

    @app.middleware("http")
    async def _auth(request: Request, call_next):
        if request.url.path == "/health" or request.method == "OPTIONS":
            # /health is open by design; OPTIONS is the CORS preflight
            # (no custom headers on preflight requests).
            return await call_next(request)
        provided = request.headers.get("X-Auth-Token", "")
        if not provided:
            # <img> tags cannot send headers; media endpoints accept ?token=.
            provided = request.query_params.get("token", "")
        if not secrets.compare_digest(provided, app.state.token):
            # Return directly: HTTPException raised in middleware is not
            # converted to a response by FastAPI's exception handlers.
            return JSONResponse(status_code=401, content={"detail": "invalid sidecar token"})
        return await call_next(request)

    # -- health --------------------------------------------------------

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "contracts_version": 1}

    # -- tasks ----------------------------------------------------------

    @app.post("/tasks/local")
    def import_local(body: dict) -> dict:
        paths = body.get("paths") or []
        if not paths:
            raise HTTPException(400, "paths is required")
        created, missing = [], []
        for raw in paths:
            path = Path(raw).expanduser()
            if not path.is_file():
                missing.append(raw)
                continue
            task = app.state.store.create_task(source_type="local", file_path=str(path))
            created.append(task)
        return {"created": created, "missing": missing}

    @app.post("/tasks/douyin")
    def import_douyin(body: dict) -> dict:
        links = [str(x).strip() for x in (body.get("links") or []) if str(x).strip()]
        if not links:
            raise HTTPException(400, "links is required")
        created = [
            app.state.store.create_task(
                source_type="douyin", file_path="", source_url=link
            )
            for link in links
        ]
        return {"created": created}

    @app.get("/douyin/check-cookie")
    def check_douyin_cookie() -> dict:
        from sidecar.download.api import DouyinClient

        cookie = _load_settings(app).douyin_cookie
        if not cookie:
            return {"status": "invalid", "reason": "未配置抖音 Cookie"}
        client = DouyinClient(cookie)
        try:
            return client.check_login()
        finally:
            client.close()

    @app.get("/tasks")
    def list_tasks(
        status: str | None = None, result_type: str | None = None,
        source_type: str | None = None,
    ) -> list[dict]:
        return app.state.store.list_tasks(
            status=status, result_type=result_type, source_type=source_type
        )

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        task = app.state.store.get_task(task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        return task

    @app.get("/tasks/{task_id}/result")
    def get_result(task_id: str) -> dict:
        task = app.state.store.get_task(task_id)
        if task is None:
            raise HTTPException(404, "task not found")
        for row in app.state.store.list_artifacts(task_id):
            if row["stage"] == "classify":
                return row["payload"]
        raise HTTPException(404, "no classification result yet")

    @app.get("/tasks/{task_id}/frames")
    def list_frames(task_id: str) -> list[dict]:
        """Perception frame preview list (docs/06 §3.1). Images themselves are
        served by /tasks/{id}/frames/{index}.jpg with ?token= for <img> tags."""
        task = _require_task(app, task_id)
        if not task.get("file_hash") or app.state.work_root is None:
            return []
        frames_dir = app.state.work_root / task["file_hash"][:16] / "frames"
        if not frames_dir.is_dir():
            return []
        return [
            {"index": i, "url": f"/tasks/{task_id}/frames/{i}.jpg?token={app.state.token}",
             "name": p.name}
            for i, p in enumerate(sorted(frames_dir.glob("frame_*.jpg")))
        ]

    @app.get("/tasks/{task_id}/frames/{name}")
    def get_frame(task_id: str, name: str) -> StreamingResponse:
        import re as _re

        if not _re.fullmatch(r"\d{4}\.jpg", name):
            raise HTTPException(400, "bad frame name")
        task = _require_task(app, task_id)
        if not task.get("file_hash") or app.state.work_root is None:
            raise HTTPException(404, "no frames")
        path = app.state.work_root / task["file_hash"][:16] / "frames" / f"frame_{name}"
        if not path.is_file():
            raise HTTPException(404, "no frames")
        return StreamingResponse(path.open("rb"), media_type="image/jpeg")


    @app.post("/tasks/{task_id}/cancel")
    def cancel_task(task_id: str) -> dict:
        task = _require_task(app, task_id)
        if task["status"] in ("classified", "rejected", "cancelled"):
            raise HTTPException(409, f"cannot cancel a task in status {task['status']}")
        app.state.store.update_task(task_id, status="cancelled", stage="cancelled")
        app.state.bus.publish(task_id, "done", {"task_id": task_id, "status": "cancelled"})
        return _require_task(app, task_id)

    @app.post("/tasks/{task_id}/retry")
    def retry_task(task_id: str) -> dict:
        task = _require_task(app, task_id)
        if task["status"] not in ("failed",):
            raise HTTPException(409, f"retry requires status failed (got {task['status']})")
        app.state.store.update_task(task_id, status="created", stage="created",
                                    error_stage=None, error_msg=None)
        return _require_task(app, task_id)

    @app.post("/tasks/{task_id}/reclassify")
    def reclassify_task(task_id: str) -> dict:
        task = _require_task(app, task_id)
        if task["status"] not in ("classified", "failed", "rejected", "cancelled"):
            raise HTTPException(409, f"cannot reclassify from status {task['status']}")
        app.state.store.update_task(task_id, status="created", stage="created",
                                    error_stage=None, error_msg=None)
        return _require_task(app, task_id)

    @app.delete("/tasks/{task_id}")
    def delete_task(task_id: str) -> dict:
        _require_task(app, task_id)
        task = app.state.store.get_task(task_id)
        app.state.store.delete_task(task_id)
        if app.state.work_root and task and task.get("file_hash"):
            import shutil

            shutil.rmtree(app.state.work_root / task["file_hash"][:16], ignore_errors=True)
        return {"deleted": task_id}

    # -- SSE --------------------------------------------------------------

    @app.get("/tasks/{task_id}/events")
    async def task_events(task_id: str) -> StreamingResponse:
        _require_task(app, task_id)
        sub, replay = app.state.bus.subscribe(task_id)

        async def stream():
            try:
                for record in replay:
                    yield _sse(record)
                while True:
                    if app.state.store.get_task(task_id) is None:
                        yield "event: deleted\ndata: {}\n\n"
                        return
                    try:
                        record = await asyncio.to_thread(sub.get, True, 0.5)
                    except Exception:  # queue.Empty after timeout -> keepalive
                        yield ": keepalive\n\n"
                        continue
                    yield _sse(record)
            finally:
                app.state.bus.unsubscribe(task_id, sub)

        return StreamingResponse(stream(), media_type="text/event-stream")

    # -- stats & CSV --------------------------------------------------------

    @app.get("/stats")
    def stats() -> dict:
        tasks = app.state.store.list_tasks()
        by_type: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for t in tasks:
            if t.get("result_type"):
                by_type[t["result_type"]] = by_type.get(t["result_type"], 0) + 1
            by_status[t["status"]] = by_status.get(t["status"], 0) + 1
        return {"total": len(tasks), "by_type": by_type, "by_status": by_status}

    @app.get("/export/tasks.csv")
    def export_csv(ids: str | None = None, result_type: str | None = None) -> StreamingResponse:
        if ids:
            tasks = []
            for tid in ids.split(","):
                task = app.state.store.get_task(tid.strip())
                if task:
                    tasks.append(task)
        else:
            tasks = app.state.store.list_tasks(result_type=result_type)
        taxonomy = get_taxonomy(app)
        name_map = {t["name_en"] or t["name"]: t["name"]
                    for t in taxonomy["types"]}
        rows = []
        for task in tasks:
            result = None
            for row in app.state.store.list_artifacts(task["id"]):
                if row["stage"] == "classify":
                    result = row["payload"]
            if not result:
                continue
            top3 = sorted(result["probabilities"].items(),
                          key=lambda kv: -kv[1])[:3]
            rows.append([
                Path(task["file_path"] or "").name,
                task.get("source_url") or "",
                result["type"],
                name_map.get(result["type"], result["type"]),
                "; ".join(f"{k} {v:.2f}" for k, v in top3),
                f"{result['confidence']:.2f}",
                "VLM直出" if result.get("degraded") else "Jev",
                task["updated_at"],
                task["file_path"] or "",
            ])
        buf = io.StringIO()
        buf.write("\ufeff")  # UTF-8 BOM for Excel (D33)
        writer = csv.writer(buf)
        writer.writerow(["文件名", "来源URL", "type", "类型显示名", "top3类型及概率",
                         "置信度", "判定层", "分类时间", "本地路径"])
        writer.writerows(rows)
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition":
                                          "attachment; filename=tasks.csv"})

    # -- V2: analysis / report / templates ---------------------------------

    @app.post("/tasks/{task_id}/analyze")
    def analyze_task(task_id: str, body: dict | None = None) -> dict:
        body = body or {}
        task = _require_task(app, task_id)
        allowed = ("classified", "done", "no_value", "failed", "rejected", "cancelled")
        if task["status"] not in allowed:
            raise HTTPException(409, f"cannot analyze from status {task['status']}")
        if task["status"] == "rejected" and (task.get("error_stage") == "extracting"):
            raise HTTPException(409, task.get("error_msg") or "任务不可分析")
        app.state.store.put_artifact(task_id, "analysis_request", 1, {
            "template_id": body.get("template_id") or "tpl-general",
            "instructions": body.get("instructions") or "",
            "override": bool(body.get("override")),
        })
        app.state.store.update_task(task_id, status="extracting", stage="extracting",
                                    error_stage=None, error_msg=None)
        return _require_task(app, task_id)

    @app.get("/tasks/{task_id}/report")
    def get_report(task_id: str) -> dict:
        _require_task(app, task_id)
        report = app.state.store.latest_report(task_id)
        if report is None:
            raise HTTPException(404, "no report yet")
        return report

    @app.get("/tasks/{task_id}/video")
    def get_video(task_id: str) -> StreamingResponse:
        import mimetypes

        task = _require_task(app, task_id)
        if not task.get("file_path"):
            raise HTTPException(404, "no video file")
        path = Path(task["file_path"])
        if not path.is_file():
            raise HTTPException(404, "video file missing on disk")
        media_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
        return StreamingResponse(path.open("rb"), media_type=media_type)

    @app.get("/templates")
    def list_templates() -> list[dict]:
        return app.state.store.list_templates()

    @app.post("/templates")
    def create_template(body: dict) -> dict:
        name = (body.get("name") or "").strip()
        user_layer = body.get("user_layer") or ""
        if not name or not user_layer:
            raise HTTPException(422, "name and user_layer are required")
        template_id = app.state.store.upsert_template(
            template_id=None, name=name, system_layer=None, user_layer=user_layer,
        )
        template = app.state.store.get_template(template_id)
        assert template is not None
        return template

    @app.delete("/templates/{template_id}")
    def delete_template(template_id: str) -> dict:
        try:
            app.state.store.delete_template(template_id)
        except KeyError as e:
            raise HTTPException(404, "template not found") from e
        except ValueError as e:
            raise HTTPException(409, str(e)) from e
        return {"deleted": template_id}

    # -- taxonomy ------------------------------------------------------------

    @app.get("/taxonomy")
    def read_taxonomy() -> dict:
        return _taxonomy_json(app.state.store)

    @app.put("/taxonomy")
    def put_taxonomy(body: dict) -> dict:
        try:
            taxonomy = load_taxonomy(body)
        except TaxonomyError as e:
            raise HTTPException(422, str(e)) from e
        app.state.store.set_setting(
            TAXONOMY_SETTING_KEY,
            json.dumps(taxonomy.model_dump(), ensure_ascii=False),
        )
        return taxonomy.model_dump()

    @app.post("/taxonomy/reset")
    def reset_taxonomy() -> dict:
        app.state.store.set_setting(
            TAXONOMY_SETTING_KEY,
            json.dumps(builtin_taxonomy().model_dump(), ensure_ascii=False),
        )
        return _taxonomy_json(app.state.store)

    # -- settings ---------------------------------------------------------------

    @app.get("/settings")
    def get_settings() -> dict:
        try:
            s = _load_settings(app)
            keys_ok = {"dashscope": True, "typesafe": True,
                       "douyin": s.douyin_cookie is not None}
            return {
                "configured": keys_ok,
                "params": {
                    "frame_count": s.frame_count,
                    "frame_max_edge": s.frame_max_edge,
                    "confidence_threshold": s.confidence_threshold,
                    "vlm_model": s.dashscope_vlm_model,
                    "jev_model": s.jev_model,
                },
                "stored": app.state.store.all_settings(),
            }
        except ConfigError as e:
            return {"configured": {"dashscope": False, "typesafe": False, "douyin": False},
                    "error": str(e), "stored": app.state.store.all_settings()}

    @app.put("/settings/params")
    def put_params(body: dict) -> dict:
        allowed = {"frame_count", "frame_max_edge", "confidence_threshold",
                   "vlm_model", "jev_model", "concurrency"}
        for key, value in (body or {}).items():
            if key not in allowed:
                raise HTTPException(422, f"unknown setting: {key}")
            app.state.store.set_setting(key, str(value))
        return {"ok": True, "stored": app.state.store.all_settings()}

    @app.put("/settings/keys")
    def put_keys(body: dict) -> dict:
        allowed = {"DASHSCOPE_API_KEY", "TYPESAFE_API_KEY", "ZHIPU_API_KEY",
                   "ARK_API_KEY", "DOUYIN_COOKIE"}
        unknown = set(body or {}) - allowed
        if unknown:
            raise HTTPException(422, f"unknown keys: {sorted(unknown)}")
        env_file = app.state.settings_file or _default_env_file()
        existing = _read_env_file(env_file)
        merged = {**existing, **{k: v for k, v in body.items() if v}}
        env_file.write_text(
            "".join(f"{k}={v}\n" for k, v in merged.items() if v), encoding="utf-8"
        )
        env_file.chmod(0o600)
        return {"ok": True, "written": sorted(body or {})}

    return app


def _sse(record: dict) -> str:
    return f"event: {record['event']}\ndata: {json.dumps(record['data'], ensure_ascii=False)}\n\n"


def _require_task(app: FastAPI, task_id: str) -> dict:
    task = app.state.store.get_task(task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    return task


def _seed_taxonomy(store: Store) -> None:
    if store.get_setting(TAXONOMY_SETTING_KEY) is None:
        store.set_setting(
            TAXONOMY_SETTING_KEY,
            json.dumps(builtin_taxonomy().model_dump(), ensure_ascii=False),
        )


def _taxonomy_json(store: Store) -> dict:
    raw = store.get_setting(TAXONOMY_SETTING_KEY)
    return json.loads(raw) if raw else builtin_taxonomy().model_dump()


def get_taxonomy(app: FastAPI) -> dict:
    return _taxonomy_json(app.state.store)


def _default_env_file() -> Path:
    from sidecar.config import DEFAULT_ENV_FILE

    return DEFAULT_ENV_FILE


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip().strip('"').strip("'")
    return result


def _load_settings(app: FastAPI) -> Settings:
    from sidecar.config import DEFAULT_ENV_FILE

    overrides = {
        k: v for k, v in app.state.store.all_settings().items()
        if k in ("DASHSCOPE_VLM_MODEL", "JEV_MODEL")
    }
    env = {**app.state.settings_env, **overrides}
    return load_settings(env, env_file=app.state.settings_file or DEFAULT_ENV_FILE)


def _build_classifier(app: FastAPI):
    from sidecar.classify.factory import build_classifier
    from sidecar.taxonomy import load_taxonomy as _load

    settings = _load_settings(app)
    taxonomy = _load(json.dumps(_taxonomy_json(app.state.store)))
    return build_classifier(settings, taxonomy, work_root=_work_default())


def _build_douyin_client(app: FastAPI):
    from sidecar.download.api import DouyinClient

    cookie = _load_settings(app).douyin_cookie or ""
    return DouyinClient(cookie)


def _work_default() -> Path:
    from sidecar.classify.factory import DATA_ROOT

    return DATA_ROOT / "work"


def _build_analyzer(app: FastAPI):
    from sidecar.analyzer.pipeline import Analyzer
    from sidecar.classify.factory import DATA_ROOT
    from sidecar.providers.asr import AsrProvider
    from sidecar.providers.chat import ChatProvider

    settings = _load_settings(app)
    return Analyzer(
        chat=ChatProvider(base_url=settings.dashscope_base_url,
                          api_key=settings.dashscope_api_key,
                          model=settings.dashscope_vlm_model),
        asr=AsrProvider(base_url=settings.dashscope_native_base_url,
                        api_key=settings.dashscope_api_key),
        work_root=DATA_ROOT / "work",
        model=settings.dashscope_vlm_model,
    )
