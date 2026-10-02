"""API tests: token auth, import, taxonomy, task lifecycle endpoints, CSV,
SSE replay. Worker disabled; state manipulated directly through the store."""


import pytest
from fastapi.testclient import TestClient

from sidecar.api.app import create_app
from sidecar.store import Store


@pytest.fixture()
def client(tmp_path) -> TestClient:
    app = create_app(
        db_path=tmp_path / "t.db",
        token="test-token",
        start_worker=False,
        work_root=tmp_path / "work",
        settings_env={},
        settings_file=tmp_path / ".env.local",  # isolate from the real one
    )
    with TestClient(app) as c:
        yield c


def auth(client: TestClient) -> dict:
    return {"X-Auth-Token": "test-token"}


class TestAuth:
    def test_health_open(self, client):
        assert client.get("/health").json()["ok"] is True

    def test_missing_token_401(self, client):
        assert client.get("/tasks").status_code == 401

    def test_wrong_token_401(self, client):
        assert client.get("/tasks", headers={"X-Auth-Token": "nope"}).status_code == 401

    def test_valid_token_passes(self, client):
        assert client.get("/tasks", headers=auth(client)).status_code == 200


class TestImport:
    def test_import_creates_tasks(self, client, tmp_path):
        f = tmp_path / "a.mp4"
        f.write_bytes(b"x")
        r = client.post("/tasks/local", headers=auth(client),
                        json={"paths": [str(f), str(tmp_path / "missing.mp4")]})
        assert r.status_code == 200
        body = r.json()
        assert len(body["created"]) == 1
        assert body["created"][0]["status"] == "created"
        assert len(body["missing"]) == 1

    def test_import_empty_paths_400(self, client):
        assert client.post("/tasks/local", headers=auth(client),
                           json={"paths": []}).status_code == 400


class TestTaxonomy:
    def test_default_seeded(self, client):
        tax = client.get("/taxonomy", headers=auth(client)).json()
        assert tax["version"] >= 1
        assert len(tax["types"]) == 11

    def test_put_updates(self, client):
        tax = client.get("/taxonomy", headers=auth(client)).json()
        tax["types"][0]["hint"] = "新提示"
        r = client.put("/taxonomy", headers=auth(client), json=tax)
        assert r.status_code == 200
        updated = client.get("/taxonomy", headers=auth(client)).json()
        assert updated["types"][0]["hint"] == "新提示"

    def test_put_invalid_422(self, client):
        r = client.put("/taxonomy", headers=auth(client),
                       json={"version": 1, "types": [{"name": "x"}]})
        assert r.status_code == 422

    def test_reset_restores_builtin(self, client):
        tax = client.get("/taxonomy", headers=auth(client)).json()
        tax["types"] = tax["types"][:2] + [{"name": "其他", "name_en": "other",
                                            "worthy": True, "hint": "x"}]
        client.put("/taxonomy", headers=auth(client), json=tax)
        reset = client.post("/taxonomy/reset", headers=auth(client)).json()
        assert len(reset["types"]) == 11


class TestTaskLifecycle:
    def _make_task(self, client, tmp_path, **fields):
        f = tmp_path / f"{fields.get('file_path', 'v')}.mp4"
        f.write_bytes(b"x")
        body = client.post("/tasks/local", headers=auth(client),
                           json={"paths": [str(f)]}).json()
        task = body["created"][0]
        if fields:
            store: Store = client.app.state.store
            store.update_task(task["id"], **fields)
        return task["id"]

    def test_get_task_404(self, client):
        assert client.get("/tasks/nope", headers=auth(client)).status_code == 404

    def test_result_requires_artifact(self, client, tmp_path):
        tid = self._make_task(client, tmp_path)
        assert client.get(f"/tasks/{tid}/result", headers=auth(client)).status_code == 404

    def test_cancel_then_reclassify(self, client, tmp_path):
        tid = self._make_task(client, tmp_path, status="classifying", stage="classifying")
        cancelled = client.post(f"/tasks/{tid}/cancel", headers=auth(client)).json()
        assert cancelled["status"] == "cancelled"
        again = client.post(f"/tasks/{tid}/reclassify", headers=auth(client)).json()
        assert again["status"] == "created"

    def test_retry_only_from_failed(self, client, tmp_path):
        tid = self._make_task(client, tmp_path, status="classified", stage="classified")
        assert client.post(f"/tasks/{tid}/retry", headers=auth(client)).status_code == 409
        client.app.state.store.update_task(tid, status="failed", error_stage="classifying")
        ok = client.post(f"/tasks/{tid}/retry", headers=auth(client)).json()
        assert ok["status"] == "created"

    def test_delete(self, client, tmp_path):
        tid = self._make_task(client, tmp_path)
        assert client.delete(f"/tasks/{tid}", headers=auth(client)).status_code == 200
        assert client.get(f"/tasks/{tid}", headers=auth(client)).status_code == 404


class TestStatsAndCsv:
    def _classified_task(self, client, tmp_path, type_="monologue"):
        tid = self._tid(client, tmp_path)
        store: Store = client.app.state.store
        store.update_task(tid, status="classified", stage="classified",
                          result_type=type_, confidence=0.9)
        store.put_artifact(tid, "classify", 1, {
            "type": type_, "type_name": "口播", "probabilities": {type_: 0.9},
            "confidence": 0.9, "degraded": False,
            "file": "a.mp4", "file_hash": "h" * 64, "taxonomy_version": 1,
        })
        return tid

    def _tid(self, client, tmp_path):
        f = tmp_path / "a.mp4"
        f.write_bytes(b"x")
        body = client.post("/tasks/local", headers=auth(client),
                           json={"paths": [str(f)]}).json()
        return body["created"][0]["id"]

    def test_stats_counts(self, client, tmp_path):
        self._classified_task(client, tmp_path)
        stats = client.get("/stats", headers=auth(client)).json()
        assert stats["by_type"]["monologue"] == 1
        assert stats["total"] >= 1

    def test_csv_export(self, client, tmp_path):
        self._classified_task(client, tmp_path)
        r = client.get("/export/tasks.csv", headers=auth(client))
        assert r.status_code == 200
        text = r.text
        assert text.startswith("\ufeff")  # UTF-8 BOM (D33)
        lines = text.lstrip("\ufeff").strip().splitlines()
        assert len(lines) == 2  # header + 1 row
        header = lines[0].split(",")
        assert len(header) == 9
        assert "monologue" in lines[1]

    def test_csv_export_selected_ids(self, client, tmp_path):
        self._classified_task(client, tmp_path)
        r = client.get("/export/tasks.csv", headers=auth(client), params={"ids": "nope"})
        assert r.status_code == 200
        assert r.text.lstrip("\ufeff").strip().splitlines().__len__() == 1  # header only


class TestSse:
    # SSE over TestClient deadlocks on the infinite generator, so the wire
    # behavior is covered by EventBus replay + _sse formatting here; the live
    # stream is verified when the packaged app runs.
    def test_events_unknown_task_404(self, client):
        assert client.get("/tasks/nope/events", headers=auth(client)).status_code == 404

    def test_bus_replay_and_sse_format(self, client, tmp_path):
        from sidecar.api.app import _sse

        f = tmp_path / "a.mp4"
        f.write_bytes(b"x")
        task = client.post("/tasks/local", headers=auth(client),
                           json={"paths": [str(f)]}).json()["created"][0]
        client.app.state.bus.publish(
            task["id"], "stage_changed",
            {"task_id": task["id"], "stage": "probing", "at": "2026-10-02T00:00:00+00:00"},
        )
        sub, replay = client.app.state.bus.subscribe(task["id"])
        client.app.state.bus.unsubscribe(task["id"], sub)
        assert replay[0]["event"] == "stage_changed"
        wire = _sse(replay[0])
        assert wire.startswith("event: stage_changed\n")
        assert wire.endswith("\n\n")
        assert '"stage": "probing"' in wire


class TestSettings:
    def test_settings_reports_unconfigured(self, client):
        body = client.get("/settings", headers=auth(client)).json()
        assert body["configured"]["dashscope"] is False

    def test_put_params_roundtrip(self, client):
        r = client.put("/settings/params", headers=auth(client),
                       json={"frame_count": "6", "concurrency": "4"})
        assert r.status_code == 200
        stored = client.get("/settings", headers=auth(client)).json()["stored"]
        assert stored["frame_count"] == "6"

    def test_put_params_unknown_422(self, client):
        assert client.put("/settings/params", headers=auth(client),
                          json={"nope": "1"}).status_code == 422

    def test_put_keys_writes_env_file(self, client, tmp_path):
        env_file = tmp_path / ".env.local"
        client.app.state.settings_file = env_file
        r = client.put("/settings/keys", headers=auth(client),
                       json={"DASHSCOPE_API_KEY": "sk-test"})
        assert r.status_code == 200
        text = env_file.read_text()
        assert "DASHSCOPE_API_KEY=sk-test" in text

        assert env_file.stat().st_mode & 0o777 == 0o600

    def test_put_keys_rejects_unknown(self, client):
        assert client.put("/settings/keys", headers=auth(client),
                          json={"SECRET": "x"}).status_code == 422
