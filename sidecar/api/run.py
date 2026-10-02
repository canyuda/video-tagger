"""Sidecar process entry: bind 127.0.0.1 random port, print one handshake
line for the Tauri shell, then serve.

Handshake (single stdout line, machine-readable):
    SIDECAR_READY {"port": 51723, "token": "..."}

Usage (PyInstaller target):
    video-tagger-sidecar [--db PATH] [--work-root PATH] [--concurrency N]
"""

from __future__ import annotations

import argparse
import json
import secrets
import socket
import sys
from pathlib import Path


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="video-tagger-sidecar")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--work-root", type=Path, default=None)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--env-file", type=Path, default=None)
    args = parser.parse_args(argv)

    db_path = args.db or _app_data_dir() / "tasks.db"
    # Stable per-user dir — NOT the PyInstaller _MEIPASS (random per launch,
    # wiped on exit): frames/cache must survive restarts.
    work_root = args.work_root or _app_data_dir() / "work"
    env_file = args.env_file or _resolve_env_file()

    import uvicorn

    from sidecar.api.app import create_app

    token = secrets.token_urlsafe(32)
    port = free_port()
    app = create_app(
        db_path=db_path,
        work_root=work_root,
        token=token,
        concurrency=args.concurrency,
        settings_file=env_file,
    )
    print(f"SIDECAR_READY {json.dumps({'port': port, 'token': token})}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
    return 0


def _resolve_env_file() -> Path:
    """Keys live in .env.local: the repo one during development, the app-data
    one in a packaged build (PROJECT_ROOT points into the PyInstaller temp
    extraction dir, so it cannot be used there)."""
    import os

    from sidecar.config import PROJECT_ROOT

    override = os.environ.get("VIDEOTAGGER_ENV_FILE")
    if override:
        return Path(override)
    dev_file = PROJECT_ROOT / ".env.local"
    if dev_file.is_file():
        return dev_file
    return _app_data_dir() / ".env.local"


def _app_data_dir() -> Path:
    """Per-user app data dir (sidecar database). Env override wins."""
    import os

    override = os.environ.get("VIDEOTAGGER_DATA_DIR")
    if override:
        return Path(override)
    base = {
        "darwin": Path.home() / "Library" / "Application Support" / "video-tagger",
        "win32": Path.home() / "AppData" / "Roaming" / "video-tagger",
    }.get(sys.platform)
    if base is None:
        base = Path.home() / ".local" / "share" / "video-tagger"
    base.mkdir(parents=True, exist_ok=True)
    return base


if __name__ == "__main__":
    sys.exit(main())
