"""Contract freeze tests (D31): exported artifacts stay in sync with the
Pydantic source of truth."""

import json
import subprocess
import sys
from pathlib import Path

from sidecar.contracts import CONTRACTS_VERSION, STAGES, TASK_STATUSES
from sidecar.observations import OBSERVATION_TAGS

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "shared"


def test_exports_are_up_to_date():
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_contracts.py")],
        capture_output=True, text=True, check=True,
    )
    # Regeneration must not change tracked artifacts (run twice -> same bytes).
    first = {p.name: p.read_text() for p in (SHARED / "schemas").glob("*.json")}
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_contracts.py")],
        capture_output=True, text=True, check=True,
    )
    second = {p.name: p.read_text() for p in (SHARED / "schemas").glob("*.json")}
    assert first == second
    assert "task-events.schema.json" in first


def test_events_schema_covers_frozen_stages():
    schema = json.loads((SHARED / "schemas" / "task-events.schema.json").read_text())
    assert schema["stages"] == list(STAGES)
    assert schema["task_statuses"] == list(TASK_STATUSES)
    for name in ("StageChanged", "Progress", "TaskError", "Done"):
        assert name in schema["definitions"]


def test_types_ts_mentions_all_contracts():
    ts = (SHARED / "types.ts").read_text()
    for needle in (
        "StageChanged", "Progress", "TaskError", "Done",
        "TaxonomyType", "Observation", "OBSERVATION_TAGS",
        f"CONTRACTS_VERSION = {CONTRACTS_VERSION}",
    ):
        assert needle in ts, f"types.ts missing {needle}"
    assert json.dumps(OBSERVATION_TAGS[0]) in ts
