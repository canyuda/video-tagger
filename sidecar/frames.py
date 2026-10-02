"""V2 keyframe extraction (docs/03 §3.3).

Four-layer strategy: scene cuts (ffmpeg scene select) + max-interval fallback
+ phash dedup (hamming <= 10) + total cap (default 150, uniform thinning).
Frames: long edge 768px JPEG q75, filenames carry the second index
(frame_0035.jpg) for prompt time anchors.

Pure decision logic is split from ffmpeg/PIL I/O for zero-cost tests.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from sidecar.media import MediaError

DEFAULT_MAX_INTERVAL = 5.0
DEFAULT_SCENE_THRESHOLD = 0.35
DEFAULT_MAX_FRAMES = 150
DENSE_INTERVAL = 1.5
DENSE_MAX_EDGE = 1024
PHASH_HAMMING_LIMIT = 10

_SHOWINFO_RE = re.compile(r"pts_time:([0-9.]+)")


@dataclass(frozen=True)
class FrameRef:
    timestamp: float
    path: Path


# -- pure selection logic ---------------------------------------------------


def fallback_timestamps(
    duration: float, interval: float, existing: list[float]
) -> list[float]:
    """Force a frame whenever a gap between consecutive kept timestamps
    (including 0 and duration) exceeds `interval`."""
    if duration <= 0:
        raise ValueError("duration must be positive")
    points = sorted({0.0, *existing, duration})
    needed: list[float] = []
    for start, end in zip(points, points[1:], strict=False):
        gap = end - start
        if gap > interval:
            count = int(gap // interval)
            for k in range(1, count + 1):
                ts = round(start + gap * k / (count + 1), 3)
                if ts < end - 0.05:
                    needed.append(ts)
    return needed


def thin_to_cap(timestamps: list[float], cap: int) -> list[float]:
    """Uniformly reduce to <= cap, preserving order and endpoints."""
    if len(timestamps) <= cap:
        return timestamps
    ordered = sorted(timestamps)
    step = (len(ordered) - 1) / (cap - 1)
    picked = sorted({round(ordered[int(i * step)], 3) for i in range(cap)})
    return picked


def dense_sampling_needed(transcript_chars: int, duration_s: float,
                          chars_per_minute_threshold: float = 5.0) -> bool:
    """Transcript below the threshold -> no narration -> dense sampling."""
    if duration_s <= 0:
        return False
    chars_per_minute = transcript_chars / (duration_s / 60.0)
    return chars_per_minute < chars_per_minute_threshold


def phash_bits(image) -> int:
    """64-bit perceptual hash: 32x32 grayscale -> DCT-II -> top-left 8x8 median."""
    import numpy as np

    gray = np.asarray(image.convert("L").resize((32, 32)), dtype=float)
    freq = np.arange(32)[:, None]  # k (frequency) axis first for left-multiply
    spatial = np.arange(32)[None, :] + 0.5  # n (spatial) axis
    cosine = np.cos(np.pi * freq * spatial / 32.0)
    dct = cosine @ gray @ cosine.T  # type: ignore[operator]
    low = dct[:8, :8].flatten()[1:]  # skip DC term
    median = float(np.median(low))
    bits = 0
    for value in low:
        bits = (bits << 1) | (1 if value > median else 0)
    return bits


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def dedupe_by_phash(frames: list[tuple[float, int, Path]],
                     limit: int = PHASH_HAMMING_LIMIT) -> list[tuple[float, Path]]:
    """Drop near-duplicate frames (keep the earlier one). Input rows:
    (timestamp, phash, path)."""
    kept: list[tuple[float, Path]] = []
    hashes: list[int] = []
    for timestamp, digest, path in frames:
        if any(hamming_distance(digest, h) <= limit for h in hashes):
            continue
        kept.append((timestamp, path))
        hashes.append(digest)
    return kept


# -- ffmpeg / PIL I/O ---------------------------------------------------------


def scene_cut_timestamps(
    video: Path, threshold: float = DEFAULT_SCENE_THRESHOLD, ffmpeg: str = "ffmpeg"
) -> list[float]:
    """Scene-cut timestamps via ffmpeg select+showinfo (stderr parse)."""
    cmd = [
        ffmpeg, "-v", "info", "-i", str(video),
        "-vf", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        raise MediaError(f"scene detection failed: {e}") from e
    timestamps = [float(m) for m in _SHOWINFO_RE.findall(result.stderr)]
    return sorted({round(t, 3) for t in timestamps})


def extract_frame(video: Path, timestamp: float, out_path: Path,
                  max_edge: int = 768, ffmpeg: str = "ffmpeg") -> Path:
    scale = f"scale='min({max_edge},iw)':'min({max_edge},ih)':force_original_aspect_ratio=decrease"
    cmd = [
        ffmpeg, "-v", "error", "-y", "-ss", f"{timestamp:.3f}", "-i", str(video),
        "-frames:v", "1", "-vf", scale, "-q:v", "4", str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or not out_path.is_file():
        raise MediaError(f"frame extraction failed at {timestamp}s")
    return out_path


def extract_keyframes(
    video: Path,
    duration: float,
    out_dir: Path,
    *,
    interval: float = DEFAULT_MAX_INTERVAL,
    max_frames: int = DEFAULT_MAX_FRAMES,
    max_edge: int = 768,
    ffmpeg: str = "ffmpeg",
) -> list[FrameRef]:
    """Full V2 keyframe pipeline for one video."""
    out_dir.mkdir(parents=True, exist_ok=True)
    scene_ts = scene_cut_timestamps(video, ffmpeg=ffmpeg)
    fallback = fallback_timestamps(duration, interval, scene_ts)
    candidates = sorted({*scene_ts, *fallback})
    kept: list[tuple[float, int, Path]] = []
    from PIL import Image

    for ts in candidates:
        path = out_dir / f"frame_{int(ts):04d}.jpg"
        try:
            extract_frame(video, ts, path, max_edge=max_edge, ffmpeg=ffmpeg)
            with Image.open(path) as image:
                digest = phash_bits(image)
        except MediaError:
            continue
        kept.append((ts, digest, path))
    deduped = dedupe_by_phash(kept)
    thinned = thin_to_cap([ts for ts, _ in deduped], max_frames)
    keep_set = set(thinned)
    return [
        FrameRef(ts, path)
        for ts, path in deduped
        if ts in keep_set
    ]
