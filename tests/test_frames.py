"""Tests for V2 keyframe pure logic (docs/03 §3.3) + phash."""

import pytest

from sidecar.frames import (
    dedupe_by_phash,
    dense_sampling_needed,
    fallback_timestamps,
    hamming_distance,
    phash_bits,
    thin_to_cap,
)


class TestFallbackTimestamps:
    def test_fills_long_gaps(self):
        # one 30s gap with interval 5 -> ~5 evenly placed frames
        needed = fallback_timestamps(30.0, 5.0, [])
        assert len(needed) >= 4
        assert all(0 < t < 30 for t in needed)

    def test_no_fill_when_scenes_cover(self):
        # scene cuts every 4s -> no gaps > 5s
        scenes = [4.0, 8.0, 12.0, 16.0]
        assert fallback_timestamps(20.0, 5.0, scenes) == []

    def test_invalid_duration_raises(self):
        with pytest.raises(ValueError):
            fallback_timestamps(0, 5.0, [])


class TestThinToCap:
    def test_keeps_endpoints_and_count(self):
        ts = [float(i) for i in range(0, 240, 5)]  # 48 candidates
        thinned = thin_to_cap(ts, 30)
        assert len(thinned) <= 30
        assert 0.0 in thinned and 235.0 in thinned

    def test_noop_under_cap(self):
        assert thin_to_cap([1.0, 2.0, 3.0], 10) == [1.0, 2.0, 3.0]


class TestDenseSampling:
    def test_silent_video_triggers_dense(self):
        assert dense_sampling_needed(9, 120.0) is True  # 4.5 chars/min

    def test_boundary_not_below_threshold(self):
        assert dense_sampling_needed(10, 120.0) is False  # exactly 5/min

    def test_narrated_video_does_not(self):
        assert dense_sampling_needed(2000, 120.0) is False

    def test_zero_duration_safe(self):
        assert dense_sampling_needed(100, 0) is False


class TestPhash:
    def test_identical_images_same_hash(self):
        from PIL import Image

        image = Image.new("L", (64, 64), 128)
        assert phash_bits(image) == phash_bits(image)

    def test_different_images_far_apart(self):
        from PIL import Image

        def make(mode):
            img = Image.new("L", (64, 64))
            img.putdata([
                (x * 4) if mode == "h" else (y * 4) if mode == "v" else
                (255 if (x // 8 + y // 8) % 2 else 0)
                for y in range(64) for x in range(64)
            ])
            return img

        h, v, checker = make("h"), make("v"), make("checker")
        assert hamming_distance(phash_bits(h), phash_bits(v)) > 10
        assert hamming_distance(phash_bits(h), phash_bits(checker)) > 10

    def test_dedupe_keeps_first(self, tmp_path):
        rows = [
            (0.0, 0xFFFF, tmp_path / "a.jpg"),
            (5.0, 0xFFFF, tmp_path / "b.jpg"),       # duplicate (distance 0)
            (6.0, 0xFFFF ^ 0x00FF, tmp_path / "b2.jpg"),  # distance 8 <= 10
            (10.0, 0x0000, tmp_path / "c.jpg"),       # distance 16 > 10: kept
        ]
        kept = dedupe_by_phash(rows)
        assert [ts for ts, _ in kept] == [0.0, 10.0]

    def test_hamming(self):
        assert hamming_distance(0b1010, 0b1010) == 0
        assert hamming_distance(0b1010, 0b0101) == 4
