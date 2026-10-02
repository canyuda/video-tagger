"""ffmpeg/ffprobe wrappers: probing, frame extraction, audio sample extraction.

Command construction is pure (unit-tested); execution goes through subprocess
with stderr captured. Binary paths default to PATH lookup and are overridable
via environment (FFMPEG_PATH / FFPROBE_PATH) — never hardcoded call sites.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class MediaError(Exception):
    """Media processing failure. ffprobe/ffmpeg failure on a given file is a
    TERMINAL error (corrupt/unsupported file, docs/04 §6) — no silent retry."""


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    has_video: bool
    has_audio: bool
    width: int | None = None
    height: int | None = None


def probe_command(ffprobe: str, path: Path) -> list[str]:
    return [
        ffprobe, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ]


def parse_probe(text: str) -> MediaInfo:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise MediaError(f"ffprobe output is not JSON: {e}") from e
    try:
        duration = float(data["format"]["duration"])
    except (KeyError, TypeError, ValueError) as e:
        raise MediaError(f"ffprobe output missing duration: {e}") from e
    streams = data.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    first = videos[0] if videos else {}
    return MediaInfo(
        duration=duration,
        has_video=bool(videos),
        has_audio=bool(audios),
        width=first.get("width"),
        height=first.get("height"),
    )


def _binary(name: str) -> str:
    """Resolution order (docs/04 §8: bundled LGPL build first):
    1. FFMPEG_PATH / FFPROBE_PATH env override
    2. bundled: ffmpeg-bin/ next to the running executable (PyInstaller
       onefile extracts to _MEIPASS; Tauri resources land beside the app bin)
    3. PATH (development)
    """
    env_override = os.environ.get("FFMPEG_PATH" if name == "ffmpeg" else "FFPROBE_PATH")
    if env_override:
        return env_override
    import sys

    base_candidates = []
    if getattr(sys, "_MEIPASS", None):  # PyInstaller onefile extraction dir
        base_candidates.append(Path(sys._MEIPASS) / "ffmpeg-bin")  # noqa: SLF001
    if getattr(sys, "frozen", False):
        base_candidates.append(Path(sys.executable).parent / "ffmpeg-bin")
    else:
        # dev/source: repo root /ffmpeg-bin
        base_candidates.append(Path(__file__).resolve().parents[1] / "ffmpeg-bin")
    for base in base_candidates:
        candidate = base / name
        if candidate.is_file():
            return str(candidate)
    return name  # PATH fallback


def probe(path: Path, ffprobe: str | None = None) -> MediaInfo:
    binary = ffprobe or _binary("ffprobe")
    result = _run(probe_command(binary, path))
    return parse_probe(result.stdout)


def frame_args(
    ffmpeg: str, src: Path, timestamp: float, out_path: Path, max_edge: int = 512
) -> list[str]:
    scale = f"scale='min({max_edge},iw)':'min({max_edge},ih)':force_original_aspect_ratio=decrease"
    return [
        ffmpeg, "-v", "error", "-y",
        "-ss", f"{timestamp:.3f}", "-i", str(src),
        "-frames:v", "1", "-vf", scale,
        "-q:v", "4", str(out_path),
    ]


def extract_frames(
    path: Path, timestamps: list[float], out_dir: Path,
    max_edge: int = 512, ffmpeg: str | None = None,
) -> list[Path]:
    """Extract one JPEG per timestamp. Output names carry the second index
    (frame_0000.jpg, ...) so prompts can reference frames in order."""
    binary = ffmpeg or _binary("ffmpeg")
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for i, t in enumerate(timestamps):
        out = out_dir / f"frame_{i:04d}.jpg"
        _run(frame_args(binary, path, t, out, max_edge))
        if not out.is_file():
            raise MediaError(f"ffmpeg produced no frame at t={t:.3f}s for {path.name}")
        outputs.append(out)
    return outputs


def audio_concat_filter(windows: list[tuple[float, float]]) -> str:
    """Build the filter_complex that trims each window and concatenates them."""
    trims = [
        f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]"
        for i, (s, e) in enumerate(windows)
    ]
    chain = "".join(f"[a{i}]" for i in range(len(windows)))
    return ";".join(trims) + f";{chain}concat=n={len(windows)}:v=0:a=1[out]"


def extract_audio_samples(
    path: Path, windows: list[tuple[float, float]], out_path: Path,
    ffmpeg: str | None = None,
) -> Path:
    """Concatenate the given audio windows into one 16kHz mono MP3 sample."""
    if not windows:
        raise MediaError("no audio windows to extract")
    binary = ffmpeg or _binary("ffmpeg")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        binary, "-v", "error", "-y", "-i", str(path),
        "-filter_complex", audio_concat_filter(windows),
        "-map", "[out]", "-ac", "1", "-ar", "16000", "-b:a", "64k",
        str(out_path),
    ]
    _run(cmd)
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise MediaError(f"ffmpeg produced no audio sample for {path.name}")
    return out_path


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError as e:
        raise MediaError(f"binary not found: {cmd[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise MediaError(f"{cmd[0]} timed out after 120s") from e
    if result.returncode != 0:
        detail = (result.stderr or "").strip().splitlines()[-1:] or ["(no stderr)"]
        raise MediaError(f"{cmd[0]} failed (exit {result.returncode}): {detail[0][:300]}")
    return result
