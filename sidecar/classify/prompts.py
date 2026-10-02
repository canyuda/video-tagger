"""Pure prompt/state builders for the classification pipeline.

All builders are pure: bytes in -> messages out. The system layers are fixed
code (docs/03 §3.0: 信息载体判定原则等不随配置变); taxonomy-driven parts
(criteria tables, type lists) come in as arguments.
"""

from __future__ import annotations

import base64
import json

from sidecar.observations import AUDIO_STYLE_TAGS, OBSERVATION_TAGS
from sidecar.taxonomy import Taxonomy, effective_key

PERCEPTION_SYSTEM = """\
You are the visual perception module of a short-video classification pipeline.
You are given uniformly sampled frames (each labeled with its timestamp) from
ONE video, plus optional platform metadata. Report only observable facts:

- tags: pick AT MOST 8 from the controlled vocabulary. Fewer, high-confidence
  tags are strongly preferred — do not enumerate everything visible.
- free_note: one short English sentence for salient facts the tags cannot
  express (e.g. "presenter holds up a product and points to an on-screen price").
- summary_zh: one Chinese sentence summarizing the visual evidence.
- other_guess: if the video looks like a form OUTSIDE the known categories,
  a short English guess of what it is; otherwise null.

Tag disambiguation rules:
- multiple-speakers: requires visible BACK-AND-FORTH dialogue (two people
  taking turns talking). A person talking to camera while others merely sit,
  listen or pass by is NOT multiple-speakers.
- gesture-performance: the person performs choreographed gestures, poses or
  hand-dance moves to camera, often with music, stickers or cute effects.
- stage-fullbody-dance: full-body dance in a stage/performance setting.

General rules:
- Facts only: do NOT decide the final content type, do not name it.
- Read prominent burned-in subtitles off the frames.
- Audio-style tags (conversational-speech / voiceover-narration / lyrics-like /
  no-speech) may ONLY be used when an audio transcript excerpt is provided."""

L2_AUDIO_SYSTEM = """\
You convert a partial ASR transcript (3 sampled audio windows, may be
out of order or incomplete) from one video into ONE audio-style observation.
Output:
- tags: exactly one best-fitting tag from [conversational-speech,
  voiceover-narration, lyrics-like, no-speech]. The transcript is
  machine-generated from real speech: if it contains natural sentences,
  speech EXISTS — never choose no-speech then. Choose no-speech only when the
  transcript is empty or non-speech noise. lyrics-like when the text is
  rhythmic/repetitive like song lyrics.
- free_note: one short English sentence (e.g. "single continuous speaker
  teaching, no dialogue" or "speech is rhythmic and repetitive, resembles
  lyrics").
- summary_zh: one Chinese sentence.
- other_guess: usually null."""

VLM_DIRECT_SYSTEM = """\
You are the classifier of a short-video pipeline. Based on the sampled frames
(and transcript excerpt if provided), classify the video into EXACTLY ONE
content type from the given option list, following the priority rules stated
in the option descriptions. Also give a confidence between 0 and 1.
The list always includes an "other" fallback — use it only when nothing fits."""


def encode_frame_jpeg(jpeg: bytes) -> str:
    return "data:image/jpeg;base64," + base64.b64encode(jpeg).decode("ascii")


def perception_messages(
    frames: list[tuple[float, bytes]],
    duration_s: float,
    metadata: dict | None = None,
) -> list[dict]:
    """L1 perception: interleaved [t=..] labels and frames."""
    meta_text = (
        json.dumps(metadata, ensure_ascii=False) if metadata else "none (local file)"
    )
    parts: list[dict] = [
        {
            "type": "text",
            "text": (
                f"Video duration: {duration_s:.1f}s. Platform metadata: {meta_text}.\n"
                f"Below are {len(frames)} uniformly sampled frames:"
            ),
        }
    ]
    for timestamp, jpeg in frames:
        parts.append({"type": "text", "text": f"[t={timestamp:.1f}s]"})
        parts.append({"type": "image_url", "image_url": {"url": encode_frame_jpeg(jpeg)}})
    return [
        {"role": "system", "content": PERCEPTION_SYSTEM},
        {"role": "user", "content": parts},
    ]


def l2_audio_messages(transcript_text: str) -> list[dict]:
    excerpt = transcript_text.strip() or "(empty transcript)"
    return [
        {"role": "system", "content": L2_AUDIO_SYSTEM},
        {"role": "user", "content": f"Transcript excerpt:\n{excerpt}"},
    ]


def vlm_direct_messages(
    frames: list[tuple[float, bytes]],
    duration_s: float,
    taxonomy: Taxonomy,
    metadata: dict | None = None,
    transcript_excerpt: str | None = None,
) -> list[dict]:
    options = "\n".join(
        f"- {effective_key(t)}: {t.hint_en or t.hint}" for t in taxonomy.types
    )
    meta_text = (
        json.dumps(metadata, ensure_ascii=False) if metadata else "none"
    )
    text = (
        f"Video duration: {duration_s:.1f}s. Platform metadata: {meta_text}.\n"
        f"Content type options (name: description):\n{options}\n"
    )
    if transcript_excerpt:
        text += f"Audio transcript excerpt:\n{transcript_excerpt}\n"
    text += f"\nBelow are {len(frames)} sampled frames. Classify the video."
    parts: list[dict] = [{"type": "text", "text": text}]
    for timestamp, jpeg in frames:
        parts.append({"type": "text", "text": f"[t={timestamp:.1f}s]"})
        parts.append({"type": "image_url", "image_url": {"url": encode_frame_jpeg(jpeg)}})
    return [
        {"role": "system", "content": VLM_DIRECT_SYSTEM},
        {"role": "user", "content": parts},
    ]


def vlm_direct_json_schema(taxonomy: Taxonomy) -> dict:
    return {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": taxonomy.effective_keys()},
            "confidence": {"type": "number"},
        },
        "required": ["type", "confidence"],
        "additionalProperties": False,
    }


def l2_json_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(AUDIO_STYLE_TAGS)},
                "maxItems": 1,
                "description": "Exactly one best-fitting audio style tag",
            },
            "free_note": {"type": ["string", "null"]},
            "summary_zh": {"type": "string"},
            "other_guess": {"type": ["string", "null"]},
        },
        "required": ["tags", "summary_zh"],
        "additionalProperties": False,
    }


def build_jev_state(
    observation_tags: list[str],
    duration_s: float,
    *,
    free_note: str | None = None,
    other_guess: str | None = None,
    metadata: dict | None = None,
    audio_transcript_excerpt: str | None = None,
    audio_free_note: str | None = None,
) -> dict:
    """English-only state for the decision layer (D07: Jev gets no CJK by
    design; the Chinese summary never enters this state)."""
    state: dict = {
        "video": {
            "duration_seconds": round(duration_s, 1),
            "source": "local-file",
            "platform_metadata": metadata,
        },
        "visual_observations": list(observation_tags),
        "visual_free_note": free_note,
        "out_of_list_guess": other_guess,
        "audio_transcript_excerpt": audio_transcript_excerpt,
        "audio_style_note": audio_free_note,
    }
    return state


def jev_questions(taxonomy: Taxonomy) -> dict[str, dict]:
    criteria = taxonomy.choice_criteria()
    return {
        "video_type": {
            "type": "choice",
            "instructions": (
                "Classify this short video into exactly one content type using the "
                "observations and metadata in `video`, `visual_observations`, "
                "`audio_transcript_excerpt`. Follow priority rules in the option "
                "descriptions (narrated gameplay or film clips are commentary, not "
                "gameplay). The transcript is decisive evidence for speech content: "
                "one continuous teaching/talking voice means monologue or tutorial "
                "even if other people appear on screen; lyrics without visible "
                "vocal performance suggest dance or music visuals. If the "
                "observations suggest a form outside the options, choose other."
            ),
            "criteria": criteria,
        },
        "speech_or_text_driven": {
            "type": "noul",
            "instructions": (
                "Is the video's content mainly carried by speech, embedded subtitles "
                "or on-screen text (rather than by music, dance movement, gameplay "
                "action, or atmosphere)?"
            ),
            "criteria": {
                "true": "Speech, subtitles or on-screen text carry the content",
                "false": "Music, movement, gameplay action or visuals carry the content",
            },
        },
    }


def transcript_excerpt(sentences: list[dict], max_chars: int = 1200) -> str:
    """Flatten ASR sentences into one excerpt, truncated to max_chars."""
    text = " ".join(s.get("text", "") for s in sentences).strip()
    return text[:max_chars]


def observation_tags_vocabulary() -> list[str]:
    return list(OBSERVATION_TAGS)
