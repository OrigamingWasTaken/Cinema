import asyncio
import json
import shutil
import subprocess

import pytest

from cinema.media import MediaError, MediaInfo, Prober, codec_string

INFO = json.dumps({
    "format": {"duration": "12.0", "format_name": "matroska,webm"},
    "streams": [
        {"codec_type": "video", "codec_name": "h264", "profile": "High", "level": 40},
        {"codec_type": "audio", "codec_name": "ac3"},
    ],
})
KEYFRAMES = "0.000000\n2.000000\n4.000000\n"


def fake_run(calls):
    async def run(args):
        calls.append(args)
        return KEYFRAMES if "csv=p=0" in args else INFO
    return run


def test_codec_strings():
    assert codec_string({"codec_name": "h264", "profile": "High", "level": 40}) == "avc1.640028"
    assert codec_string({"codec_name": "h264", "profile": "Main", "level": 31}) == "avc1.4d401f"
    assert codec_string({"codec_name": "hevc", "profile": "Main 10", "level": 120}) == "hvc1.2.4.L120.90"
    assert codec_string({"codec_name": "hevc", "profile": "Main", "level": 123}) == "hvc1.1.6.L123.90"


@pytest.mark.anyio
async def test_probe_parses_and_caches(tmp_path):
    f = tmp_path / "a.mkv"
    f.write_bytes(b"x")
    calls = []
    prober = Prober(tmp_path / "cache", fake_run(calls))
    info = await prober.probe(f)
    assert info == MediaInfo(12.0, "mkv", "h264", "avc1.640028", "ac3", (0.0, 2.0, 4.0))
    n = len(calls)
    assert await prober.probe(f) == info
    assert len(calls) == n
    # a fresh Prober reads the on-disk cache too
    assert await Prober(tmp_path / "cache", fake_run(calls)).probe(f) == info
    assert len(calls) == n
    f.write_bytes(b"xx")
    await prober.probe(f)
    assert len(calls) > n


@pytest.mark.anyio
async def test_probe_failure_is_media_error(tmp_path):
    f = tmp_path / "a.mkv"
    f.write_bytes(b"x")

    async def boom(args):
        raise MediaError("nope")

    with pytest.raises(MediaError):
        await Prober(tmp_path / "cache", boom).probe(f)


@pytest.mark.anyio
async def test_concurrent_probes_share_one_run(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    calls = []
    inner = fake_run(calls)

    async def slow(args):
        await asyncio.sleep(0.05)
        return await inner(args)

    prober = Prober(tmp_path / "cache", slow)
    a, b = await asyncio.gather(prober.probe(f), prober.probe(f))
    assert a == b and a.container == "mp4"
    assert len(calls) == 2  # one info run + one keyframe run, not four


@pytest.mark.anyio
@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg not installed")
async def test_real_ffprobe_on_generated_sample(tmp_path):
    f = tmp_path / "sample.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=24",
         "-f", "lavfi", "-i", "sine=frequency=440", "-t", "12",
         "-c:v", "libx264", "-force_key_frames", "expr:gte(t,n_forced*2)", "-sc_threshold", "0",
         "-c:a", "ac3", str(f)], check=True)
    info = await Prober(tmp_path / "cache").probe(f)
    assert info.duration == pytest.approx(12, abs=0.2)
    assert len(info.keyframes) == 6
    for got, want in zip(info.keyframes, [0, 2, 4, 6, 8, 10]):
        assert got == pytest.approx(want, abs=0.05)
    assert info.audio_codec == "ac3"
    assert info.container == "mkv"
    assert info.codec_string.startswith("avc1.")
