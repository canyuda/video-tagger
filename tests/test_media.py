"""Tests for media command construction (pure) and real ffmpeg integration.

Command builders are pure functions; the integration block runs only when
ffmpeg/ffprobe exist locally (skipped in environments without them).
"""

import json
import shutil
import subprocess

import pytest

from sidecar.media import (
    MediaError,
    MediaInfo,
    audio_concat_filter,
    extract_audio_samples,
    extract_frames,
    frame_args,
    parse_probe,
    probe,
    probe_command,
)

FFPROBE_JSON = json.dumps(
    {
        "streams": [
            {"codec_type": "video", "width": 1280, "height": 720},
            {"codec_type": "audio"},
        ],
        "format": {"duration": "12.345"},
    }
)

FFPROBE_NO_AUDIO = json.dumps(
    {
        "streams": [{"codec_type": "video", "width": 720, "height": 1280}],
        "format": {"duration": "60.0"},
    },
)


class TestParseProbe:
    def test_parses_duration_and_streams(self):
        info = parse_probe(FFPROBE_JSON)
        assert info == MediaInfo(duration=12.345, has_video=True, has_audio=True,
                                 width=1280, height=720)

    def test_video_without_audio_is_normal(self, ):
        info = parse_probe(FFPROBE_NO_AUDIO)
        assert info.has_video and not info.has_audio
        assert info.duration == 60.0

    def test_missing_duration_raises(self):
        with pytest.raises(MediaError, match="duration"):
            parse_probe(json.dumps({"streams": [{"codec_type": "video"}], "format": {}}))

    def test_garbage_json_raises(self):
        with pytest.raises(MediaError):
            parse_probe("not json{")


class TestCommandBuilders:
    def test_probe_command_shape(self, tmp_path):
        cmd = probe_command("ffprobe", tmp_path / "v.mp4")
        assert cmd[0] == "ffprobe"
        assert "-print_format" in cmd and "json" in cmd
        assert str(tmp_path / "v.mp4") in cmd

    def test_frame_args_seek_and_scale(self, tmp_path):
        src, out = tmp_path / "v.mp4", tmp_path / "f_0001.jpg"
        cmd = frame_args("ffmpeg", src, 12.5, out, max_edge=512)
        assert cmd[0] == "ffmpeg"
        assert cmd[cmd.index("-ss") + 1] == "12.500"
        assert cmd[cmd.index("-frames:v") + 1] == "1"
        vf = cmd[cmd.index("-vf") + 1]
        assert "512" in vf and "force_original_aspect_ratio=decrease" in vf

    def test_audio_concat_filter_three_windows(self):
        f = audio_concat_filter([(0.0, 5.0), (5.0, 10.0), (10.0, 15.0)])
        assert f.count("atrim") == 3
        assert f.startswith("[0:a]")
        assert "concat=n=3:v=0:a=1" in f

    def test_audio_concat_filter_single_window(self):
        f = audio_concat_filter([(0.0, 8.0)])
        assert "atrim" in f and "concat=n=1" in f


# ---------------------------------------------------------------------------
# Integration: real ffmpeg (skipped when binaries are unavailable)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory):
    """Generate a real 6s video: red test pattern + 440Hz tone."""
    if not _ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe not available")
    path = tmp_path_factory.mktemp("media") / "src.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.mark.skipif(not _ffmpeg_available(), reason="ffmpeg/ffprobe not available")
class TestMediaIntegration:
    def test_probe_real_file(self, sample_video):
        info = probe(sample_video)
        assert 5.5 <= info.duration <= 6.5
        assert info.has_video and info.has_audio

    def test_extract_frames_real_file(self, sample_video, tmp_path):
        frames = extract_frames(sample_video, [0.5, 2.5, 5.5], tmp_path, max_edge=512)
        assert len(frames) == 3
        assert all(p.is_file() and p.stat().st_size > 0 for p in frames)
        assert [p.name for p in frames] == ["frame_0000.jpg", "frame_0001.jpg", "frame_0002.jpg"]

    def test_extract_audio_samples_real_file(self, sample_video, tmp_path):
        out = extract_audio_samples(sample_video, [(0.0, 2.0), (2.0, 4.0)], tmp_path / "sample.mp3")
        assert out.is_file() and out.stat().st_size > 1000

    def test_probe_corrupt_file_raises(self, tmp_path):
        bad = tmp_path / "bad.mp4"
        bad.write_bytes(b"not a video at all")
        with pytest.raises(MediaError):
            probe(bad)
