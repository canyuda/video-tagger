"""Tests for the SQLite store: schema migration, CRUD, dedupe, settings."""

import pytest

from sidecar.store import Store


@pytest.fixture()
def store(tmp_path) -> Store:
    s = Store(tmp_path / "tasks.db")
    s.init()
    return s


def utc_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


class TestInit:
    def test_init_sets_user_version(self, tmp_path):
        s = Store(tmp_path / "a.db")
        s.init()
        with s.connect() as conn:
            assert conn.execute("PRAGMA user_version").fetchone()[0] == 1

    def test_reinit_idempotent(self, tmp_path):
        s = Store(tmp_path / "a.db")
        s.init()
        s.init()  # same version: no-op, no data loss
        task = s.create_task(source_type="local", file_path="/x.mp4")
        s.init()
        assert s.get_task(task["id"])["file_path"] == "/x.mp4"

    def test_wal_mode_enabled(self, store):
        with store.connect() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"


class TestTasks:
    def test_create_and_get_roundtrip(self, store):
        tid = store.create_task(
            source_type="local", file_path="/movies/a.mp4",
            source_meta={"duration": 12.3},
        )["id"]
        task = store.get_task(tid)
        assert task["status"] == "created"
        assert task["stage"] == "created"
        assert task["file_path"] == "/movies/a.mp4"
        assert task["source_meta"] == {"duration": 12.3}
        assert task["created_at"].endswith("+00:00")  # UTC stored

    def test_get_missing_returns_none(self, store):
        assert store.get_task("nope") is None

    def test_update_task_fields(self, store):
        tid = store.create_task(source_type="local", file_path="/a.mp4")["id"]
        store.update_task(tid, status="classified", stage="classified",
                          result_type="monologue", confidence=0.91)
        task = store.get_task(tid)
        assert task["status"] == "classified"
        assert task["result_type"] == "monologue"

    def test_list_filters(self, store):
        a = store.create_task(source_type="local", file_path="/a.mp4")
        b = store.create_task(source_type="douyin", file_path="/b.mp4")
        store.update_task(a["id"], status="classified", result_type="monologue")
        assert [t["id"] for t in store.list_tasks(status="classified")] == [a["id"]]
        assert [t["id"] for t in store.list_tasks(source_type="douyin")] == [b["id"]]
        assert {t["id"] for t in store.list_tasks()} == {a["id"], b["id"]}
        assert [t["id"] for t in store.list_tasks(result_type="monologue")] == [a["id"]]

    def test_find_by_hash(self, store):
        t = store.create_task(source_type="local", file_path="/a.mp4")
        store.update_task(t["id"], file_hash="h1")
        assert store.find_by_hash("h1")["id"] == t["id"]
        assert store.find_by_hash("h2") is None


class TestArtifacts:
    def test_put_get_roundtrip(self, store):
        tid = store.create_task(source_type="local", file_path="/a.mp4")["id"]
        store.put_artifact(tid, "classify", 1, {"type": "monologue"})
        assert store.get_artifact(tid, "classify", 1) == {"type": "monologue"}
        assert store.get_artifact(tid, "classify", 2) is None

    def test_upsert_overwrites(self, store):
        tid = store.create_task(source_type="local", file_path="/a.mp4")["id"]
        store.put_artifact(tid, "classify", 1, {"v": 1})
        store.put_artifact(tid, "classify", 1, {"v": 2})
        assert store.get_artifact(tid, "classify", 1) == {"v": 2}

    def test_list_artifacts_by_task(self, store):
        tid = store.create_task(source_type="local", file_path="/a.mp4")["id"]
        store.put_artifact(tid, "classify", 1, {"a": 1})
        store.put_artifact(tid, "perceive", 1, {"b": 2})
        stages = {row["stage"] for row in store.list_artifacts(tid)}
        assert stages == {"classify", "perceive"}


class TestSettings:
    def test_set_get(self, store):
        store.set_setting("frame_count", "10")
        assert store.get_setting("frame_count") == "10"
        assert store.get_setting("missing") is None
        store.set_setting("frame_count", "20")  # upsert
        assert store.get_setting("frame_count") == "20"

    def test_all_settings(self, store):
        store.set_setting("a", "1")
        store.set_setting("b", "2")
        assert store.all_settings() == {"a": "1", "b": "2"}
