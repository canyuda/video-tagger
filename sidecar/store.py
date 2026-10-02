"""SQLite store: tasks / artifacts / settings (+V2 reports & templates).

docs/04 §6 schema, with user_version migrations (D42) and WAL (D53).
Timestamps are UTC ISO-8601 strings. One writer at a time (queue thread);
reads go through short-lived connections. `result_type`/`confidence` are
denormalized onto tasks for cheap filtering; full payloads live in artifacts.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

_MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE tasks (
      id TEXT PRIMARY KEY,
      source_type TEXT NOT NULL,
      source_url TEXT,
      file_path TEXT,
      file_hash TEXT,
      status TEXT NOT NULL,
      stage TEXT NOT NULL,
      error_stage TEXT,
      error_msg TEXT,
      result_type TEXT,
      confidence REAL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      source_meta TEXT
    );
    CREATE INDEX idx_tasks_status ON tasks(status);
    CREATE INDEX idx_tasks_hash ON tasks(file_hash);
    CREATE INDEX idx_tasks_result_type ON tasks(result_type);

    CREATE TABLE artifacts (
      task_id TEXT NOT NULL,
      stage TEXT NOT NULL,
      version INTEGER NOT NULL,
      payload_json TEXT NOT NULL,
      PRIMARY KEY (task_id, stage, version)
    );

    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

    CREATE TABLE reports (
      id TEXT PRIMARY KEY,
      task_id TEXT NOT NULL,
      template_id TEXT,
      model TEXT,
      prompt_snapshot TEXT,
      report_json TEXT,
      markdown TEXT,
      created_at TEXT NOT NULL
    );

    CREATE TABLE templates (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      builtin INTEGER NOT NULL DEFAULT 0,
      system_layer TEXT,
      user_layer TEXT,
      variables_json TEXT,
      updated_at TEXT NOT NULL
    );
    """,
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            if current >= SCHEMA_VERSION:
                return
            for version in range(current + 1, SCHEMA_VERSION + 1):
                conn.executescript(_MIGRATIONS[version])
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # -- tasks -----------------------------------------------------------

    def create_task(
        self,
        *,
        source_type: str,
        file_path: str,
        source_url: str | None = None,
        source_meta: dict | None = None,
    ) -> dict:
        import uuid

        task_id = uuid.uuid4().hex
        now = utc_now()
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO tasks (id, source_type, source_url, file_path,
                     status, stage, created_at, updated_at, source_meta)
                   VALUES (?, ?, ?, ?, 'created', 'created', ?, ?, ?)""",
                (task_id, source_type, source_url, file_path, now, now,
                 _dump_json(source_meta)),
            )
        row = self._get_row(task_id)
        assert row is not None
        return row

    def get_task(self, task_id: str) -> dict | None:
        return self._get_row(task_id)

    def _get_row(self, task_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return row_to_task(row) if row else None

    def update_task(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "source_url", "file_path", "file_hash", "status", "stage",
            "error_stage", "error_msg", "result_type", "confidence", "source_meta",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"cannot update unknown fields: {sorted(unknown)}")
        assignments = ", ".join(f"{k} = ?" for k in fields)
        values = [
            _dump_json(v) if k == "source_meta" else v for k, v in fields.items()
        ]
        with self.connect() as conn:
            cur = conn.execute(
                f"UPDATE tasks SET {assignments}, updated_at = ? WHERE id = ?",
                (*values, utc_now(), task_id),
            )
            if cur.rowcount == 0:
                raise KeyError(f"task not found: {task_id}")

    def list_tasks(
        self,
        *,
        status: str | None = None,
        result_type: str | None = None,
        source_type: str | None = None,
    ) -> list[dict]:
        clauses, params = [], []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if result_type:
            clauses.append("result_type = ?")
            params.append(result_type)
        if source_type:
            clauses.append("source_type = ?")
            params.append(source_type)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM tasks {where} ORDER BY created_at DESC", params
            ).fetchall()
        return [row_to_task(r) for r in rows]

    def find_by_hash(self, file_hash: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM tasks WHERE file_hash = ? ORDER BY created_at DESC LIMIT 1",
                (file_hash,),
            ).fetchone()
        return row_to_task(row) if row else None

    def delete_task(self, task_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM artifacts WHERE task_id = ?", (task_id,))
            conn.execute("DELETE FROM reports WHERE task_id = ?", (task_id,))
            cur = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            if cur.rowcount == 0:
                raise KeyError(f"task not found: {task_id}")

    # -- artifacts ---------------------------------------------------------

    def put_artifact(self, task_id: str, stage: str, version: int, payload: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO artifacts (task_id, stage, version, payload_json)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT (task_id, stage, version)
                   DO UPDATE SET payload_json = excluded.payload_json""",
                (task_id, stage, version, json.dumps(payload, ensure_ascii=False)),
            )

    def get_artifact(self, task_id: str, stage: str, version: int) -> Any | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM artifacts"
                " WHERE task_id = ? AND stage = ? AND version = ?",
                (task_id, stage, version),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def list_artifacts(self, task_id: str) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT stage, version, payload_json FROM artifacts WHERE task_id = ?",
                (task_id,),
            ).fetchall()
        return [
            {"stage": r["stage"], "version": r["version"], "payload": json.loads(r["payload_json"])}
            for r in rows
        ]

    # -- settings ----------------------------------------------------------

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO settings (key, value) VALUES (?, ?)
                   ON CONFLICT (key) DO UPDATE SET value = excluded.value""",
                (key, value),
            )

    def get_setting(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def all_settings(self) -> dict[str, str]:
        with self.connect() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def requeue_inflight(self) -> int:
        """Startup crash recovery (docs/04 §6): tasks left mid-stage by a dead
        process return to the queue. Stage artifacts stay — completed stages
        are skipped via cache. Terminal states are never touched."""
        in_flight = (
            "downloading", "probing", "classifying",
            "extracting", "transcribing", "sampling", "analyzing",
        )
        placeholders = ", ".join("?" for _ in in_flight)
        with self.connect() as conn:
            cur = conn.execute(
                f"""UPDATE tasks SET status = 'created', stage = 'created',
                      updated_at = ?
                    WHERE status IN ({placeholders})""",
                (utc_now(), *in_flight),
            )
            return cur.rowcount

    # -- reports & templates (V2) ------------------------------------------

    def put_report(self, task_id: str, *, template_id: str | None, model: str,
                   prompt_snapshot: str, report_json: dict, markdown: str) -> str:
        import uuid

        report_id = uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO reports (id, task_id, template_id, model,
                     prompt_snapshot, report_json, markdown, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (report_id, task_id, template_id, model, prompt_snapshot,
                 json.dumps(report_json, ensure_ascii=False), markdown, utc_now()),
            )
        return report_id

    def latest_report(self, task_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM reports WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        report = dict(row)
        report["report_json"] = json.loads(report["report_json"])
        return report

    def list_templates(self) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM templates ORDER BY builtin DESC, name").fetchall()
        return [_row_to_template(r) for r in rows]

    def get_template(self, template_id: str) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM templates WHERE id = ?", (template_id,)).fetchone()
        return _row_to_template(row) if row else None

    def upsert_template(self, *, template_id: str | None, name: str,
                        system_layer: str | None, user_layer: str,
                        builtin: bool = False) -> str:
        import uuid

        tid = template_id or uuid.uuid4().hex
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO templates (id, name, builtin, system_layer, user_layer,
                     variables_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (id) DO UPDATE SET name = excluded.name,
                     user_layer = excluded.user_layer, updated_at = excluded.updated_at""",
                (tid, name, int(builtin), system_layer, user_layer,
                 json.dumps(["{{transcript}}", "{{frames}}", "{{metadata}}",
                             "{{user_instructions}}"]),
                 utc_now()),
            )
        return tid

    def delete_template(self, template_id: str) -> None:
        with self.connect() as conn:
            row = conn.execute("SELECT builtin FROM templates WHERE id = ?",
                               (template_id,)).fetchone()
            if row is None:
                raise KeyError(f"template not found: {template_id}")
            if row["builtin"]:
                raise ValueError("builtin templates cannot be deleted")
            conn.execute("DELETE FROM templates WHERE id = ?", (template_id,))


BUILTIN_TEMPLATES: list[dict] = [
    {"id": "tpl-general", "name": "通用总结", "builtin": True, "system_layer": None,
     "user_layer": "全面总结视频内容：核心观点、可执行步骤、金句与关键时间点。"},
    {"id": "tpl-meeting", "name": "会议纪要", "builtin": True, "system_layer": None,
     "user_layer": "以会议纪要格式输出：议题、结论、行动项（负责人/时间，若提及）、待办事项。"},
    {"id": "tpl-knowledge", "name": "知识点提取", "builtin": True, "system_layer": None,
     "user_layer": "提取视频中的知识点，按主题分组，每个知识点标注讲解时间点，形成学习笔记。"},
    {"id": "tpl-sales", "name": "带货话术拆解", "builtin": True, "system_layer": None,
     "user_layer": "拆解带货话术：卖点呈现顺序、价格锚定、促单话术、可信度构建，各配时间点。"},
    {"id": "tpl-factcheck", "name": "求真核查", "builtin": True, "system_layer": None,
     "user_layer": "对视频中的事实性陈述逐条核查：引用了什么证据、断言与证据的匹配度、存疑之处。"},
]


def seed_templates(store: Store) -> None:
    existing = {t["id"] for t in store.list_templates()}
    for template in BUILTIN_TEMPLATES:
        if template["id"] not in existing:
            store.upsert_template(
                template_id=template["id"], name=template["name"],
                system_layer=template["system_layer"],
                user_layer=template["user_layer"], builtin=True,
            )


def _dump_json(value: Any) -> str | None:
    return json.dumps(value, ensure_ascii=False) if value is not None else None


def row_to_task(row: sqlite3.Row) -> dict:
    task = dict(row)
    task["source_meta"] = json.loads(task["source_meta"]) if task["source_meta"] else None
    return task

def _row_to_template(row: sqlite3.Row) -> dict:
    template = dict(row)
    template["builtin"] = bool(template["builtin"])
    return template
