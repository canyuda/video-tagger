"""Perception-layer observations schema (D51, docs/03 §3.0).

Semi-controlled: a frozen vocabulary of ~20 enum tags plus free-note fields.
The same schema serves L1 (frames only) and L2 (frames + audio transcript):
audio-related tags (lyrics-like / no-speech / ...) are only picked when the
prompt supplies a transcript. This is a Phase 0.5 contract-freeze candidate.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

OBSERVATION_TAGS: tuple[str, ...] = (
    # visual composition
    "person-facing-camera",
    "multiple-speakers",
    "interview-setting",
    "stage-fullbody-dance",
    "gesture-performance",
    "product-closeup",
    "text-heavy-slide",
    "screen-recording-ui",
    "hud-ui-game",
    "subtitle-bar-embedded",
    "on-screen-stickers",
    "outdoor-scenery",
    "cinematic-edit",
    "animation-heavy",
    "food-closeup",
    "pets-or-animals",
    "talking-head-with-broll",
    # speech / audio style (picked only when audio evidence is supplied)
    "conversational-speech",
    "voiceover-narration",
    "lyrics-like",
    "no-speech",
)


# Enum built from the frozen vocabulary (single source of truth). StrEnum so
# members compare equal to their string values everywhere.
ObservationTag = StrEnum(  # type: ignore[no-redef]
    "ObservationTag", {tag.replace("-", "_").upper(): tag for tag in OBSERVATION_TAGS}
)

# Tags that describe AUDIO evidence. L1 (frames only) has no audio evidence,
# so these are stripped from L1 output; L2 (with transcript) supplies them.
AUDIO_STYLE_TAGS = frozenset(
    {"conversational-speech", "voiceover-narration", "lyrics-like", "no-speech"}
)


class Observation(BaseModel):
    """Structured perception output. Tags only state observed facts, never a
    final classification — the decision layer (Jev) does that."""

    tags: list[ObservationTag]
    free_note: str | None = Field(
        default=None,
        description="Short English note for forms the tags cannot express (optional)",
    )
    summary_zh: str = Field(description="One-sentence Chinese evidence summary for UI display")
    other_guess: str | None = Field(
        default=None,
        description="If content looks like none of the known types, an English guess of the form",
    )

    @field_validator("tags")
    @classmethod
    def _no_duplicates(cls, v: list[ObservationTag]) -> list[ObservationTag]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate observation tags")
        return v


def validate_observation(raw: dict) -> Observation:
    """Parse/validate a raw VLM output dict into an Observation."""
    normalized = dict(raw)
    for key in ("free_note", "other_guess"):
        value = normalized.get(key)
        if isinstance(value, str) and not value.strip():
            normalized[key] = None
    return Observation.model_validate(normalized)


def without_audio_tags(observation: Observation) -> Observation:
    """L1 guard: frames carry no audio evidence, so audio-style tags picked by
    the VLM anyway (instruction violation) are stripped deterministically."""
    kept = [t for t in observation.tags if t not in AUDIO_STYLE_TAGS]
    return observation.model_copy(update={"tags": kept})


def merge_audio_tags(
    visual_tags: list[str], audio_tags: list[str], transcript_nonempty: bool
) -> list[str]:
    """Merge L2 audio tags onto visual tags, with the hard rule that a
    non-empty machine transcript proves speech exists: `no-speech` is dropped
    if present alongside speech-style tags or with a non-empty transcript."""
    audio = list(audio_tags)
    speech_tags = [t for t in audio if t != "no-speech"]
    if transcript_nonempty:
        audio = speech_tags  # non-empty transcript => no-speech is impossible
    merged = list(dict.fromkeys([*visual_tags, *audio]))
    return merged


def perception_json_schema() -> dict:
    """JSON Schema handed to the VLM for structured output (enum-constrained)."""
    return {
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "string", "enum": list(OBSERVATION_TAGS)},
                "maxItems": 8,
                "description": "Observed facts about the video; pick only from the enum, at most 8",
            },
            "free_note": {
                "type": ["string", "null"],
                "description": "Optional short English note for forms the tags cannot express",
            },
            "summary_zh": {
                "type": "string",
                "description": "One-sentence Chinese evidence summary",
            },
            "other_guess": {
                "type": ["string", "null"],
                "description": "Optional English guess if the form seems outside the taxonomy",
            },
        },
        "required": ["tags", "summary_zh"],
        "additionalProperties": False,
    }
