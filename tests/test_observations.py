"""Tests for the perception-layer observations schema (D51, docs/03 §3.0).

The schema is a V1 contract candidate: controlled enum tags (~20) + free_note.
It backs VLM structured output, so JSON-schema generation must work too.
"""

import pytest
from pydantic import ValidationError

from sidecar.observations import (
    AUDIO_STYLE_TAGS,
    OBSERVATION_TAGS,
    Observation,
    merge_audio_tags,
    perception_json_schema,
    validate_observation,
    without_audio_tags,
)


class TestObservationTags:
    def test_v1_vocabulary_size_about_20(self):
        # D51: "约 20 个受控枚举标签" — allow small drift while iterating.
        assert 15 <= len(OBSERVATION_TAGS) <= 25

    def test_docs_named_tags_present(self):
        expected = {
            "person-facing-camera", "multiple-speakers", "hud-ui-game",
            "stage-fullbody-dance", "subtitle-bar-embedded", "product-closeup",
            "text-heavy-slide", "conversational-speech", "lyrics-like", "no-speech",
            "outdoor-scenery", "cinematic-edit", "talking-head-with-broll",
        }
        assert expected <= set(OBSERVATION_TAGS)

    def test_tags_are_stable_lowercase_hyphen_identifiers(self):
        for tag in OBSERVATION_TAGS:
            assert tag == tag.lower()
            assert " " not in tag


class TestObservationModel:
    def test_valid_observation(self):
        o = Observation(
            tags=["person-facing-camera", "subtitle-bar-embedded"],
            free_note="speaker gestures at a chart",
            summary_zh="人物面对镜头口播，画面下方有字幕条",
            other_guess=None,
        )
        assert o.tags[0] == "person-facing-camera"

    def test_unknown_tag_rejected(self):
        with pytest.raises(ValidationError):
            Observation(tags=["not-a-real-tag"], free_note=None, summary_zh="x", other_guess=None)

    def test_empty_tags_allowed(self):
        o = Observation(tags=[], free_note=None, summary_zh="x", other_guess=None)
        assert o.tags == []

    def test_duplicate_tags_rejected(self):
        # State sent to Jev must stay stable: no duplicate noise.
        with pytest.raises(ValidationError):
            Observation(
                tags=["no-speech", "no-speech"], free_note=None, summary_zh="x", other_guess=None
            )

    def test_validate_observation_from_vlm_dict(self):
        raw = {
            "tags": ["hud-ui-game"],
            "free_note": None,
            "summary_zh": "游戏画面",
            "other_guess": "",
        }
        o = validate_observation(raw)
        assert o.tags == ["hud-ui-game"]
        assert o.other_guess in (None, "")


class TestJsonSchemaForVlm:
    def test_schema_generated_for_structured_output(self):
        schema = perception_json_schema()
        assert schema["type"] == "object"
        props = schema["properties"]
        assert set(props) == {"tags", "free_note", "summary_zh", "other_guess"}
        # Enum-constrained tags so the VLM cannot invent labels, capped to
        # prevent tag explosions on ambiguous footage.
        assert set(props["tags"]["items"]["enum"]) == set(OBSERVATION_TAGS)
        assert props["tags"]["maxItems"] == 8
        assert schema["required"] == ["tags", "summary_zh"]

    def test_schema_is_valid_json_dumpable(self):
        import json

        json.dumps(perception_json_schema())


class TestAudioTagGuards:
    def test_without_audio_tags_strips_l1_violations(self):
        o = Observation(
            tags=["person-facing-camera", "conversational-speech", "no-speech"],
            free_note=None, summary_zh="x", other_guess=None,
        )
        stripped = without_audio_tags(o)
        assert stripped.tags == ["person-facing-camera"]

    def test_without_audio_tags_keeps_visual_tags_only(self):
        o = Observation(
            tags=["hud-ui-game", "outdoor-scenery"], free_note=None,
            summary_zh="x", other_guess=None,
        )
        assert without_audio_tags(o).tags == ["hud-ui-game", "outdoor-scenery"]

    def test_audio_style_tags_subset_of_vocabulary(self):
        assert set(OBSERVATION_TAGS) >= AUDIO_STYLE_TAGS


class TestMergeAudioTags:
    def test_merges_without_duplicates(self):
        merged = merge_audio_tags(["person-facing-camera"], ["conversational-speech"], True)
        assert merged == ["person-facing-camera", "conversational-speech"]

    def test_nonempty_transcript_drops_no_speech(self):
        merged = merge_audio_tags(["stage-fullbody-dance"], ["no-speech", "lyrics-like"], True)
        assert "no-speech" not in merged
        assert "lyrics-like" in merged

    def test_empty_transcript_keeps_no_speech(self):
        merged = merge_audio_tags(["outdoor-scenery"], ["no-speech"], False)
        assert merged == ["outdoor-scenery", "no-speech"]

    def test_l1_audio_leakage_does_not_survive_merge(self):
        # If an audio tag somehow leaked into visual tags, dedupe keeps one.
        merged = merge_audio_tags(["conversational-speech"], ["conversational-speech"], True)
        assert merged.count("conversational-speech") == 1
