import asyncio
import json
import shutil
import subprocess

import pytest

from cinema.media import MediaError, MediaInfo, Prober, codec_string, run_tool

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


def fake_run_with(info: dict, keyframes: str):
    async def run(args):
        return keyframes if "csv=p=0" in args else json.dumps(info)
    return run


@pytest.mark.anyio
async def test_keyframes_are_relative_to_the_file_start(tmp_path):
    f = tmp_path / "a.mkv"
    f.write_bytes(b"x")
    info = {"format": {"duration": "12.0", "start_time": "1.500000", "format_name": "matroska"},
            "streams": [{"codec_type": "video", "codec_name": "h264"}]}
    got = await Prober(tmp_path / "cache", fake_run_with(info, "1.500000\n3.500000\n5.5\n")).probe(f)
    assert got.keyframes == (0.0, 2.0, 4.0)


@pytest.mark.anyio
async def test_keyframes_drop_negatives_and_start_at_zero(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    info = {"format": {"duration": "12.0", "start_time": "0.000000", "format_name": "mov,mp4"},
            "streams": [{"codec_type": "video", "codec_name": "h264"}]}
    got = await Prober(tmp_path / "cache", fake_run_with(info, "-0.083\n0.042\n2.042\n")).probe(f)
    assert got.keyframes == (0.0, 0.042, 2.042)
    assert str(got.keyframes[0]) == "0.0"


@pytest.mark.anyio
async def test_probe_missing_file_is_media_error(tmp_path):
    with pytest.raises(MediaError):
        await Prober(tmp_path / "cache", fake_run([])).probe(tmp_path / "gone.mkv")


@pytest.fixture
def spawned(monkeypatch):
    procs = []
    real = asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        proc = await real(*args, **kwargs)
        procs.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    return procs


@pytest.mark.anyio
async def test_run_tool_times_out_and_kills_the_child(spawned):
    with pytest.raises(MediaError, match="timed out"):
        await run_tool(["sleep", "30"], timeout=0.2)
    assert await asyncio.wait_for(spawned[0].wait(), 2) == -9


@pytest.mark.anyio
async def test_run_tool_cancelled_kills_the_child(spawned):
    task = asyncio.ensure_future(run_tool(["sleep", "30"]))
    while not spawned:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.wait_for(spawned[0].wait(), 2) == -9


@pytest.mark.anyio
async def test_second_probe_is_served_from_memory(tmp_path, monkeypatch):
    f = tmp_path / "a.mkv"
    f.write_bytes(b"x")
    prober = Prober(tmp_path / "cache", fake_run([]))
    info = await prober.probe(f)

    def no_disk(cached):
        raise AssertionError("read the JSON cache again")

    monkeypatch.setattr(Prober, "_read", staticmethod(no_disk))
    assert await prober.probe(f) == info
