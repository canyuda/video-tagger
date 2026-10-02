"""Frozen V1 contracts (D31, docs/04 §6): the three SSE task events.

Python Pydantic is the single source of truth; scripts/export_contracts.py
generates shared/schemas/*.json and shared/types.ts from these models so the
frontend and sidecar cannot drift.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

CONTRACTS_VERSION = 1

# Task stages, in pipeline order (docs/04 §6 state machine).
STAGES = (
    "created",     # task row exists, not yet picked up
    "downloading", # douyin source only (Phase 2)
    "probing",     # ffprobe / boundary checks
    "classifying", # perception + decision (+L2)
    "extracting",  # V2: keyframes
    "transcribing",# V2: full ASR
    "sampling",    # V2: dense sampling when no narration
    "analyzing",   # V2: report generation
)

# V1 terminal states.
TASK_STATUSES = (
    "created", "downloading", "probing", "classifying",
    "extracting", "transcribing", "sampling", "analyzing",
    "classified",  # V1 terminal: every local task ends here
    "done",        # V2 terminal: analysis report produced
    "no_value",    # V2: high-confidence unworthy, user may override
    "rejected",    # V2: >20min, analysis only
    "cancelled",   # user-cancel terminal
    "failed",      # stage retryable failure
)


class StageChanged(BaseModel):
    """SSE event `stage_changed`: a task entered a new stage."""
    task_id: str
    stage: str = Field(description="one of STAGES")
    at: str = Field(description="UTC ISO-8601 timestamp")


class Progress(BaseModel):
    """SSE event `progress`: fine-grained progress within a stage."""
    task_id: str
    stage: str
    percent: float = Field(ge=0, le=100)
    detail: str | None = None


class TaskError(BaseModel):
    """SSE event `error`: a stage failed (retryable or terminal)."""
    task_id: str
    stage: str
    message: str
    retryable: bool


class Done(BaseModel):
    """SSE event `done`: task reached a terminal state."""
    task_id: str
    status: str = Field(description="classified | done | no_value | rejected | cancelled | failed")
    report_id: str | None = None


class TaskEvent(BaseModel):
    """SSE envelope: `event` names the payload type, `data` is the payload."""
    event: str = Field(description="stage_changed | progress | error | done")
    data: StageChanged | Progress | TaskError | Done
