"""Tests for prompt/state builders (pure)."""

from sidecar.classify.prompts import (
    build_jev_state,
    encode_frame_jpeg,
    jev_questions,
    l2_audio_messages,
    l2_json_schema,
    perception_messages,
    transcript_excerpt,
    vlm_direct_json_schema,
    vlm_direct_messages,
)
from sidecar.taxonomy import builtin_taxonomy

FRAMES = [(1.5, b"\xff\xd8fake"), (5.0, b"\xff\xd8fake2")]


class TestPerceptionMessages:
    def test_shape_and_frame_labels(self):
        msgs = perception_messages(FRAMES, 12.0)
        assert msgs[0]["role"] == "system"
        user = msgs[1]
        assert user["role"] == "user"
        parts = user["content"]
        texts = [p["text"] for p in parts if p["type"] == "text"]
        images = [p for p in parts if p["type"] == "image_url"]
        assert len(images) == 2
        assert "[t=1.5s]" in texts and "[t=5.0s]" in texts
        assert "duration: 12.0s" in texts[0]

    def test_images_are_base64_data_urls(self):
        msgs = perception_messages(FRAMES, 12.0)
        url = [p for p in msgs[1]["content"] if p["type"] == "image_url"][0]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,")
        assert encode_frame_jpeg(b"\xff\xd8fake") in url

    def test_metadata_included_when_present(self):
        msgs = perception_messages(FRAMES, 12.0, metadata={"desc": "花絮", "music": "原声"})
        assert "花絮" in msgs[1]["content"][0]["text"]


class TestL2Audio:
    def test_messages_carry_transcript(self):
        msgs = l2_audio_messages("今天聊聊怎么涨粉")
        assert "今天聊聊怎么涨粉" in msgs[1]["content"]

    def test_empty_transcript_marked(self):
        msgs = l2_audio_messages("   ")
        assert "empty transcript" in msgs[1]["content"]

    def test_schema_limits_to_audio_tags(self):
        schema = l2_json_schema()
        enum = schema["properties"]["tags"]["items"]["enum"]
        assert "lyrics-like" in enum
        assert "person-facing-camera" not in enum


class TestVlmDirect:
    def test_messages_include_criteria_table(self):
        t = builtin_taxonomy()
        msgs = vlm_direct_messages(FRAMES, 30.0, t)
        text = msgs[1]["content"][0]["text"]
        assert "- monologue:" in text
        assert "- gameplay:" in text

    def test_schema_enum_matches_taxonomy_keys(self):
        t = builtin_taxonomy()
        schema = vlm_direct_json_schema(t)
        assert set(schema["properties"]["type"]["enum"]) == set(t.effective_keys())


class TestJevStateAndQuestions:
    def test_state_is_english_structured(self):
        state = build_jev_state(
            ["person-facing-camera", "subtitle-bar-embedded"],
            63.4,
            free_note="presenter gestures at a chart",
            other_guess=None,
            audio_transcript_excerpt="how to grow followers",
            audio_free_note="continuous natural speech",
        )
        assert state["visual_observations"] == [
            "person-facing-camera", "subtitle-bar-embedded",
        ]
        assert state["video"]["duration_seconds"] == 63.4
        assert state["audio_transcript_excerpt"] == "how to grow followers"

    def test_state_contains_no_cjk(self):
        # D07: Jev receives English-only state — Chinese summary never enters.
        import json

        state = build_jev_state(["person-facing-camera"], 30.0,
                                free_note="note", audio_transcript_excerpt="text")
        assert not any("\u4e00" <= ch <= "\u9fff" for ch in json.dumps(state, ensure_ascii=False))

    def test_questions_criteria_from_taxonomy(self):
        t = builtin_taxonomy()
        questions = jev_questions(t)
        q = questions["video_type"]
        assert q["type"] == "choice"
        assert set(q["criteria"]) == set(t.effective_keys())
        assert "Voice-over narration" in q["criteria"]["commentary"]
        assert questions["speech_or_text_driven"]["type"] == "noul"


class TestTranscriptExcerpt:
    def test_flattens_and_truncates(self):
        sentences = [{"text": f"句子{i}"} for i in range(200)]
        excerpt = transcript_excerpt(sentences, max_chars=100)
        assert len(excerpt) <= 100

    def test_empty(self):
        assert transcript_excerpt([]) == ""
