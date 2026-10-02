"""Analysis pipeline orchestration (docs/03 §2): audio -> ASR -> keyframes
(conditional density) -> analyzer -> report. Stage artifacts cached by the
caller; failures are terminal-but-retryable at the analyzing stage."""

from __future__ import annotations

import time
from pathlib import Path

from pydantic import BaseModel

from sidecar.analyzer.markdown import report_to_markdown
from sidecar.analyzer.prompts import (
    REPORT_JSON_SCHEMA,
    Report,
    analysis_messages,
    format_transcript,
)
from sidecar.frames import (
    DENSE_INTERVAL,
    DENSE_MAX_EDGE,
    dense_sampling_needed,
    extract_keyframes,
)
from sidecar.media import extract_audio_samples
from sidecar.providers.asr import AsrProvider
from sidecar.providers.chat import ChatProvider

MAX_ANALYSIS_SECONDS = 20 * 60  # V2 hard limit (D03)
TRANSCRIBE_STAGE_VERSION = 1
KEYFRAMES_STAGE_VERSION = 1


class AnalysisError(Exception):
    def __init__(self, message: str, *, terminal: bool = False) -> None:
        super().__init__(message)
        self.terminal = terminal


class AnalysisOutput(BaseModel):
    report: dict
    markdown: str
    transcript_text: str
    frame_count: int
    dense_sampling: bool
    timings_s: dict[str, float]
    usage_tokens: dict[str, int]
    model: str


class Analyzer:
    def __init__(
        self,
        *,
        chat: ChatProvider,
        asr: AsrProvider,
        work_root: Path,
        model: str = "qwen3-vl-flash",
    ) -> None:
        self.chat = chat
        self.asr = asr
        self.work_root = work_root
        self.model = model

    def check_duration(self, duration_s: float) -> None:
        if duration_s > MAX_ANALYSIS_SECONDS:
            raise AnalysisError(
                f"超出分析时长上限 20 分钟（该视频 {duration_s / 60:.0f} 分钟）", terminal=True
            )

    def transcribe(self, video: Path, file_hash: str) -> list[dict]:
        """Full-audio transcription. Caller caches by (file_hash, 'transcribe', v)."""
        audio = extract_audio_samples(video, [(0.0, MAX_ANALYSIS_SECONDS + 3600)],
                                      self._dir(file_hash) / "audio_full.mp3")
        # extract_audio_samples clamps windows to the real duration.
        sentences = [
            {"text": s.text, "begin_ms": s.begin_ms, "end_ms": s.end_ms}
            for s in self.asr.transcribe(audio)
        ]
        return sentences

    def keyframes(self, video: Path, file_hash: str, duration_s: float,
                  transcript_chars: int) -> tuple[list[tuple[float, Path]], bool]:
        """Conditional keyframes: dense when no narration (docs/03 §3.2)."""
        dense = dense_sampling_needed(transcript_chars, duration_s)
        frames = extract_keyframes(
            video, duration_s, self._dir(file_hash) / "keyframes",
            interval=DENSE_INTERVAL if dense else 5.0,
            max_edge=DENSE_MAX_EDGE if dense else 768,
        )
        return [(f.timestamp, f.path) for f in frames], dense  # type: ignore[return-value]

    def analyze(
        self,
        *,
        video: Path,
        file_hash: str,
        duration_s: float,
        sentences: list[dict],
        frames: list[tuple[float, Path]],
        template_name: str,
        template_instructions: str,
        user_instructions: str = "",
        metadata: dict | None = None,
    ) -> AnalysisOutput:
        timings: dict[str, float] = {}
        transcript_text = format_transcript(sentences)
        frame_payload = [(ts, p.read_bytes()) for ts, p in frames]

        t0 = time.perf_counter()
        messages = analysis_messages(
            transcript_text=transcript_text,
            frames=frame_payload,
            duration_s=duration_s,
            template_name=template_name,
            template_instructions=template_instructions,
            user_instructions=user_instructions,
            metadata=metadata,
        )
        output = self.chat.request_json(messages, REPORT_JSON_SCHEMA)
        timings["analyze"] = time.perf_counter() - t0

        report = Report.model_validate(output.data).model_dump()
        report.setdefault("meta", {})
        report["meta"].update({"model": self.model, "template": template_name,
                               "duration": round(duration_s, 1)})
        return AnalysisOutput(
            report=report,
            markdown=report_to_markdown(report),
            transcript_text=transcript_text,
            frame_count=len(frames),
            dense_sampling=False,  # set by caller via keyframes()
            timings_s={k: round(v, 2) for k, v in timings.items()},
            usage_tokens={"analyze_in": output.input_tokens or 0,
                          "analyze_out": output.output_tokens or 0},
            model=self.model,
        )

    def _dir(self, file_hash: str) -> Path:
        return self.work_root / file_hash[:16]
