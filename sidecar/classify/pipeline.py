"""Classification pipeline orchestration (docs/03 §3.0).

Perceive (VLM, frames -> English observations) -> decide (Jev Choice).
Low confidence (< threshold) triggers L2: 3x10s audio samples -> paraformer-v2
-> audio-style observations -> re-decide. Jev failure degrades to VLM-direct
classification, marked in the result (D12). Every classified video produces a
result (D14) — L2 failure keeps the L1 decision with an error note.
"""

from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel

from sidecar.cache import FileCache
from sidecar.classify import prompts
from sidecar.config import Settings
from sidecar.hashing import sha256_file
from sidecar.media import MediaError, MediaInfo, extract_audio_samples, extract_frames, probe
from sidecar.observations import (
    merge_audio_tags,
    perception_json_schema,
    validate_observation,
    without_audio_tags,
)
from sidecar.providers.asr import AsrProvider
from sidecar.providers.chat import ChatProvider
from sidecar.providers.errors import ProviderError
from sidecar.providers.jev import JevClient
from sidecar.sampling import audio_sample_windows, sample_frame_timestamps
from sidecar.taxonomy import Taxonomy, effective_key

# Bump when perception prompt or frame sampling changes.
PERCEIVE_STAGE_VERSION = 2
L2_ASR_STAGE_VERSION = 1
RESULT_REVISION = 1


class ClassificationError(Exception):
    def __init__(self, message: str, *, terminal: bool) -> None:
        super().__init__(message)
        self.terminal = terminal


class Decision(BaseModel):
    type: str
    probabilities: dict[str, float]
    confidence: float
    decision_layer: str  # "jev" | "vlm-direct"
    degraded: bool
    speech_driven: float | None = None
    usage_tokens: dict[str, int] = {}


class ClassificationResult(BaseModel):
    file: str
    file_hash: str
    taxonomy_version: int
    type: str
    type_name: str
    probabilities: dict[str, float]
    confidence: float
    analysis_worthy: bool
    summary_zh: str
    observations: list[str]
    free_note: str | None
    other_guess: str | None
    l2_triggered: bool
    l2_transcript_excerpt: str | None
    l2_audio_note: str | None = None
    decision_layer: str
    degraded: bool
    speech_driven: float | None
    timings_s: dict[str, float]
    usage_tokens: dict[str, int]
    models: dict[str, str | None]
    notes: list[str] = []


class Classifier:
    def __init__(
        self,
        *,
        settings: Settings,
        taxonomy: Taxonomy,
        chat: ChatProvider,
        jev: JevClient,
        asr: AsrProvider | None = None,
        cache: FileCache | None = None,
        work_root: Path,
        use_cache: bool = True,
    ) -> None:
        self.settings = settings
        self.taxonomy = taxonomy
        self.chat = chat
        self.jev = jev
        self.asr = asr
        self.cache = cache
        self.work_root = work_root
        self.use_cache = use_cache

    def classify(self, video_path: Path, *, fast_mode: bool = False) -> ClassificationResult:
        timings: dict[str, float] = {}
        usage: dict[str, int] = {}
        notes: list[str] = []

        t0 = time.perf_counter()
        file_hash = sha256_file(video_path)
        cached = self._cached_result(file_hash)
        if cached is not None:
            return cached
        try:
            info = probe(video_path)
        except MediaError as e:
            raise ClassificationError(f"ffprobe failed: {e}", terminal=True) from e
        if not info.has_video:
            raise ClassificationError(
                f"{video_path.name}: no video stream (audio-only files are rejected)",
                terminal=True,
            )
        timings["probe"] = time.perf_counter() - t0

        # -- perception ---------------------------------------------------
        t1 = time.perf_counter()
        timestamps = sample_frame_timestamps(info.duration, self.settings.frame_count)
        frame_files = extract_frames(
            video_path, timestamps, self._work_dir(file_hash) / "frames",
            max_edge=self.settings.frame_max_edge,
        )
        frames = [(t, p.read_bytes()) for t, p in zip(timestamps, frame_files, strict=True)]
        timings["frames"] = time.perf_counter() - t1

        observation = self._perceive(file_hash, frames, info.duration, timings, usage)

        # -- decision -----------------------------------------------------
        decision = self._decide(observation, info, frames, timings, usage, notes)
        excerpt: str | None = None
        audio_note: str | None = None
        l2_triggered = False

        if (
            decision.decision_layer == "jev"
            and decision.confidence < self.settings.confidence_threshold
            and not fast_mode
            and info.has_audio
            and self.asr is not None
        ):
            l2_triggered = True
            t3 = time.perf_counter()
            l2 = self._run_l2(
                file_hash, video_path, info, observation, frames, timings, usage, notes
            )
            timings["l2_total"] = time.perf_counter() - t3
            if l2 is not None:
                decision, excerpt, observation, audio_note = l2

        return self._build_result(
            video_path, file_hash, decision, observation, info,
            l2_triggered, excerpt, audio_note, timings, usage, notes,
        )

    # -- stages ----------------------------------------------------------

    def _perceive(
        self,
        file_hash: str,
        frames: list[tuple[float, bytes]],
        duration: float,
        timings: dict[str, float],
        usage: dict[str, int],
    ):
        cached = (
            self.cache.get(file_hash, "perceive", PERCEIVE_STAGE_VERSION)
            if self.cache and self.use_cache else None
        )
        if cached is not None:
            timings["perceive"] = 0.0
            return validate_observation(cached)
        t = time.perf_counter()
        messages = prompts.perception_messages(frames, duration)
        output = self.chat.request_json(messages, perception_json_schema())
        # L1 has frames only: strip audio-style tags the VLM may pick anyway.
        observation = without_audio_tags(validate_observation(output.data))
        timings["perceive"] = time.perf_counter() - t
        usage["perceive_in"] = usage.get("perceive_in", 0) + (output.input_tokens or 0)
        usage["perceive_out"] = usage.get("perceive_out", 0) + (output.output_tokens or 0)
        if self.cache:
            self.cache.put(
                file_hash, "perceive", PERCEIVE_STAGE_VERSION,
                {"tags": list(observation.tags), "free_note": observation.free_note,
                 "summary_zh": observation.summary_zh, "other_guess": observation.other_guess},
            )
        return observation

    def _decide(
        self, observation, info: MediaInfo, frames, timings, usage, notes,
        audio_excerpts: dict | None = None,
    ) -> Decision:
        audio_excerpts = audio_excerpts or {}
        state = prompts.build_jev_state(
            list(observation.tags),
            info.duration,
            free_note=observation.free_note,
            other_guess=observation.other_guess,
            audio_transcript_excerpt=audio_excerpts.get("transcript"),
            audio_free_note=audio_excerpts.get("audio_note"),
        )
        try:
            result = self.jev.ask(state, prompts.jev_questions(self.taxonomy))
            answer = result.choice("video_type")
            if answer.choice not in self.taxonomy.effective_keys():
                raise ProviderError.terminal(
                    "DEC", "UNKNOWN-CHOICE", f"Jev chose unknown option {answer.choice!r}"
                )
            speech = None
            noul = result.answers.get("speech_or_text_driven") or {}
            if isinstance(noul.get("noul"), (int, float)):
                speech = float(noul["noul"])
            usage["decide_in"] = usage.get("decide_in", 0) + (result.input_tokens or 0)
            usage["decide_out"] = usage.get("decide_out", 0) + (result.output_tokens or 0)
            return Decision(
                type=answer.choice,
                probabilities=dict(answer.probabilities),
                confidence=answer.confidence,
                decision_layer="jev",
                degraded=False,
                speech_driven=speech,
            )
        except ProviderError as e:
            notes.append(f"jev failed, degraded to VLM-direct: [{e.code}] {e}")
            return self._vlm_direct(info, frames, timings, usage,
                                    audio_excerpts.get("transcript"))

    def _vlm_direct(self, info, frames, timings, usage, transcript) -> Decision:
        t = time.perf_counter()
        messages = prompts.vlm_direct_messages(
            frames, info.duration, self.taxonomy, transcript_excerpt=transcript
        )
        output = self.chat.request_json(messages, prompts.vlm_direct_json_schema(self.taxonomy))
        timings["vlm_direct"] = timings.get("vlm_direct", 0.0) + (time.perf_counter() - t)
        usage["vlm_direct_in"] = usage.get("vlm_direct_in", 0) + (output.input_tokens or 0)
        usage["vlm_direct_out"] = usage.get("vlm_direct_out", 0) + (output.output_tokens or 0)
        chosen = output.data.get("type")
        if chosen not in self.taxonomy.effective_keys():
            raise ClassificationError(
                f"VLM-direct chose unknown type {chosen!r}", terminal=True
            )
        confidence = float(output.data.get("confidence", 0.5))
        return Decision(
            type=chosen, probabilities={chosen: confidence}, confidence=confidence,
            decision_layer="vlm-direct", degraded=True,
        )

    def _run_l2(self, file_hash, video_path, info, observation, frames, timings, usage, notes):
        """Returns (Decision, transcript excerpt, merged Observation, audio
        free_note) or None."""
        try:
            t = time.perf_counter()
            cached = (
                self.cache.get(file_hash, "l2-asr", L2_ASR_STAGE_VERSION)
                if self.cache and self.use_cache else None
            )
            if cached is not None:
                sentences = cached["sentences"]
            else:
                windows = audio_sample_windows(info.duration)
                audio_path = extract_audio_samples(
                    video_path, windows, self._work_dir(file_hash) / "audio_sample.mp3"
                )
                sentences = [
                    {"text": s.text, "begin_ms": s.begin_ms, "end_ms": s.end_ms}
                    for s in self.asr.transcribe(audio_path)
                ]
                if self.cache:
                    self.cache.put(
                        file_hash, "l2-asr", L2_ASR_STAGE_VERSION, {"sentences": sentences}
                    )
            timings["l2_asr"] = time.perf_counter() - t

            excerpt = prompts.transcript_excerpt(sentences)
            t = time.perf_counter()
            audio_obs = validate_observation(
                self.chat.request_json(
                    prompts.l2_audio_messages(excerpt), prompts.l2_json_schema()
                ).data
            )
            timings["l2_observe"] = time.perf_counter() - t
            merged_tags = merge_audio_tags(
                list(observation.tags), list(audio_obs.tags), bool(excerpt)
            )
            merged = validate_observation({
                "tags": merged_tags,
                "free_note": observation.free_note,
                "summary_zh": observation.summary_zh,
                "other_guess": observation.other_guess,
            })
            decision = self._decide(
                merged, info, frames, timings, usage, notes,
                audio_excerpts={"transcript": excerpt, "audio_note": audio_obs.free_note},
            )
            return decision, excerpt, merged, audio_obs.free_note
        except (ProviderError, MediaError) as e:
            # L2 is supplementary evidence: its failure must not lose the
            # classification itself (D14: every video yields a result).
            notes.append(f"L2 skipped: [{getattr(e, 'code', 'MEDIA')}] {e}")
            return None

    # -- helpers ----------------------------------------------------------

    def _work_dir(self, file_hash: str) -> Path:
        return self.work_root / file_hash[:16]

    def _cached_result(self, file_hash: str) -> ClassificationResult | None:
        if not self.cache or not self.use_cache:
            return None
        cached = self.cache.get(file_hash, "classify",
                                f"v{self.taxonomy.version}-r{RESULT_REVISION}")
        if cached is None:
            return None
        return ClassificationResult.model_validate(cached)

    def _build_result(
        self, video_path, file_hash, decision, observation, info,
        l2_triggered, excerpt, audio_note, timings, usage, notes,
    ) -> ClassificationResult:
        type_name = next(
            (t.name for t in self.taxonomy.types if effective_key(t) == decision.type),
            decision.type,
        )
        result = ClassificationResult(
            file=video_path.name,
            file_hash=file_hash,
            taxonomy_version=self.taxonomy.version,
            type=decision.type,
            type_name=type_name,
            probabilities=decision.probabilities,
            confidence=decision.confidence,
            analysis_worthy=any(
                t.worthy for t in self.taxonomy.types if effective_key(t) == decision.type
            ),
            summary_zh=observation.summary_zh,
            observations=list(observation.tags),
            free_note=observation.free_note,
            other_guess=observation.other_guess,
            l2_triggered=l2_triggered,
            l2_transcript_excerpt=excerpt,
            l2_audio_note=audio_note,
            decision_layer=decision.decision_layer,
            degraded=decision.degraded,
            speech_driven=decision.speech_driven,
            timings_s={k: round(v, 2) for k, v in timings.items()},
            usage_tokens=usage,
            models={
                "vlm": self.settings.dashscope_vlm_model,
                "decision": self.settings.jev_model,
                "asr": "paraformer-v2" if self.asr else None,
            },
            notes=notes,
        )
        if self.cache:
            self.cache.put(
                file_hash, "classify", f"v{self.taxonomy.version}-r{RESULT_REVISION}",
                result.model_dump(),
            )
        return result
