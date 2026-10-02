"""Tests for pure sampling math: 10-frame visual grid + 3x10s audio windows (docs/03 §3.0)."""

import pytest

from sidecar.sampling import audio_sample_windows, sample_frame_timestamps


class TestSampleFrameTimestamps:
    def test_ten_frames_for_typical_video(self):
        ts = sample_frame_timestamps(120.0)
        assert len(ts) == 10

    def test_uniform_spread_covers_start_middle_end(self):
        ts = sample_frame_timestamps(100.0)
        # Midpoint rule: first frame within the first decile, last within the last.
        assert 0 < ts[0] <= 10.0
        assert 90.0 <= ts[-1] < 100.0
        # Roughly uniform: consecutive gaps approximately equal (10s each).
        gaps = [b - a for a, b in zip(ts, ts[1:], strict=False)]
        assert max(gaps) - min(gaps) <= 0.01

    def test_strictly_increasing(self):
        ts = sample_frame_timestamps(61.7)
        assert ts == sorted(ts)
        assert len(set(ts)) == len(ts)

    def test_short_video_dedupes_repeated_timestamps(self):
        # 5-second video still "samples 10 frames" -> duplicates collapse.
        ts = sample_frame_timestamps(5.0)
        assert len(ts) == len(set(ts))
        assert 1 <= len(ts) <= 10

    def test_very_short_video_single_frame(self):
        ts = sample_frame_timestamps(0.8)
        assert ts == [pytest.approx(0.4)]

    def test_zero_or_negative_duration_raises(self):
        with pytest.raises(ValueError):
            sample_frame_timestamps(0.0)
        with pytest.raises(ValueError):
            sample_frame_timestamps(-3.0)

    def test_frame_count_override(self):
        ts = sample_frame_timestamps(90.0, count=5)
        assert len(ts) == 5


class TestAudioSampleWindows:
    def test_three_ten_second_windows(self):
        ws = audio_sample_windows(300.0)
        assert len(ws) == 3
        for start, end in ws:
            assert end - start == 10.0

    def test_positions_centered_at_10_50_90_percent(self):
        # Windows are CENTERED on 10%/50%/90% positions: 300s -> starts 25/145/265.
        ws = audio_sample_windows(300.0)
        assert [round(s) for s, _ in ws] == [25, 145, 265]

    def test_last_window_clamped_at_end(self):
        # 35s: 90% center is 31.5 -> naive start 26.5, clamped so end == duration.
        ws = audio_sample_windows(35.0)
        assert ws[-1] == (25.0, 35.0)

    def test_all_windows_within_bounds(self):
        ws = audio_sample_windows(105.0)
        assert all(0 <= s < e <= 105.0 for s, e in ws)

    def test_video_shorter_than_one_clip_single_full_window(self):
        ws = audio_sample_windows(8.0)
        assert ws == [(0.0, 8.0)]

    def test_short_video_shrinks_clips_to_keep_three_windows(self):
        # 15s: clips shrink to 5s so all three positions still get sampled.
        ws = audio_sample_windows(15.0)
        assert len(ws) == 3
        assert ws == [(0.0, 5.0), (5.0, 10.0), (10.0, 15.0)]

    def test_no_audio_overlap_between_windows(self):
        ws = audio_sample_windows(45.0)
        for (_, e1), (s2, _) in zip(ws, ws[1:], strict=False):
            assert e1 <= s2

    def test_invalid_duration_raises(self):
        with pytest.raises(ValueError):
            audio_sample_windows(0.0)
