"""Pure sampling math for classification evidence (docs/03 §3.0).

Visual: N frames evenly spread across the duration (midpoint rule so the very
first/last frames avoid pure black boundaries). Audio: 3 windows centered at
10%/50%/90%, clamped to bounds; clips shrink on short videos so all three
positions are still covered without overlap.
"""

from __future__ import annotations

import math

AUDIO_POSITIONS = (0.10, 0.50, 0.90)
AUDIO_CLIP_SECONDS = 10.0


def sample_frame_timestamps(duration: float, count: int = 10) -> list[float]:
    """Return `count` evenly spread timestamps (midpoint of each equal segment).

    For videos shorter than `count` seconds, fewer frames are taken (one per
    second at most) to avoid near-duplicate frames.
    """
    if duration <= 0:
        raise ValueError(f"duration must be positive, got {duration}")
    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")
    n = max(1, min(count, math.ceil(duration)))
    return [round((i + 0.5) * duration / n, 3) for i in range(n)]


def audio_sample_windows(
    duration: float,
    clip: float = AUDIO_CLIP_SECONDS,
    positions: tuple[float, ...] = AUDIO_POSITIONS,
) -> list[tuple[float, float]]:
    """Return (start, end) windows centered at each position, clamped to bounds.

    - duration <= clip: one full-length window [(0, duration)].
    - duration < clip * len(positions): clips shrink so every position is
      still sampled with no overlap.
    """
    if duration <= 0:
        raise ValueError(f"duration must be positive, got {duration}")
    if duration <= clip:
        return [(0.0, round(duration, 3))]

    clip_len = min(clip, duration / len(positions))
    windows: list[tuple[float, float]] = []
    for p in positions:
        start = min(max(p * duration - clip_len / 2, 0.0), duration - clip_len)
        windows.append((round(start, 3), round(start + clip_len, 3)))
    return windows
