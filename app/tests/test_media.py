import asyncio
import gc
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
# ffprobe -show_entries packet=pts_time,flags: decode order, keyframes flagged K.
KEYFRAMES = "0.000000,K__\n0.083333,___\n2.000000,K__\n1.958333,___\n4.000000,K__\nN/A,K__\n"


def fake_run(calls):
    async def run(args, timeout=None):
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
    info = await prober.indexed(f)
    assert info == MediaInfo(12.0, "mkv", "h264", "avc1.640028", "ac3", (0.0, 2.0, 4.0),
                             codec_tag="", profile_known=True)
    n = len(calls)
    assert n == 2
    assert await prober.indexed(f) == info
    assert len(calls) == n
    # a fresh Prober reads the on-disk cache too, both parts
    assert await Prober(tmp_path / "cache", fake_run(calls)).indexed(f) == info
    assert len(calls) == n
    f.write_bytes(b"xx")
    await prober.indexed(f)
    assert len(calls) > n


@pytest.mark.anyio
async def test_probe_failure_is_media_error(tmp_path):
    f = tmp_path / "a.mkv"
    f.write_bytes(b"x")

    async def boom(args, timeout=None):
        raise MediaError("nope")

    with pytest.raises(MediaError):
        await Prober(tmp_path / "cache", boom).probe(f)
    with pytest.raises(MediaError):
        await Prober(tmp_path / "cache", boom).indexed(f)


@pytest.mark.anyio
async def test_concurrent_probes_share_one_run(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    calls = []
    inner = fake_run(calls)

    async def slow(args, timeout=None):
        await asyncio.sleep(0.05)
        return await inner(args)

    prober = Prober(tmp_path / "cache", slow)
    a, b = await asyncio.gather(prober.indexed(f), prober.indexed(f))
    assert a == b and a.container == "mp4"
    assert len(calls) == 2  # one info run + one keyframe run, not four


@pytest.mark.anyio
@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg not installed")
async def test_real_ffprobe_on_generated_sample(tmp_path):
    f = tmp_path / "sample.mkv"
    subprocess.run(
        ["nice", "-n", "19", "ffmpeg", "-v", "error", "-threads", "2",
         "-f", "lavfi", "-i", "testsrc=size=160x90:rate=24",
         "-f", "lavfi", "-i", "anullsrc=sample_rate=48000", "-t", "12",
         "-c:v", "libx264", "-threads", "2", "-bf", "3",
         "-force_key_frames", "expr:gte(t,n_forced*2)", "-sc_threshold", "0",
         "-c:a", "ac3", str(f)], check=True)
    info = await Prober(tmp_path / "cache").indexed(f)
    assert info.duration == pytest.approx(12, abs=0.2)
    assert len(info.keyframes) == 6
    for got, want in zip(info.keyframes, [0, 2, 4, 6, 8, 10]):
        assert got == pytest.approx(want, abs=0.05)
    assert info.audio_codec == "ac3"
    assert info.container == "mkv"
    assert info.codec_string.startswith("avc1.")


def fake_run_with(info: dict, keyframes: str):
    async def run(args, timeout=None):
        return keyframes if "csv=p=0" in args else json.dumps(info)
    return run


@pytest.mark.anyio
async def test_keyframes_are_relative_to_the_file_start(tmp_path):
    f = tmp_path / "a.mkv"
    f.write_bytes(b"x")
    info = {"format": {"duration": "12.0", "start_time": "1.500000", "format_name": "matroska"},
            "streams": [{"codec_type": "video", "codec_name": "h264"}]}
    got = await Prober(tmp_path / "cache", fake_run_with(
        info, "1.500000,K_\n3.500000,K_\n4.5,__\n5.5,K_\n")).indexed(f)
    assert got.keyframes == (0.0, 2.0, 4.0)


@pytest.mark.anyio
async def test_keyframes_drop_negatives_and_start_at_zero(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    info = {"format": {"duration": "12.0", "start_time": "0.000000", "format_name": "mov,mp4"},
            "streams": [{"codec_type": "video", "codec_name": "h264"}]}
    got = await Prober(tmp_path / "cache", fake_run_with(
        info, "-0.083,K_\n0.042,K_\n2.042,K_\n")).indexed(f)
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
    info = await prober.indexed(f)

    def no_disk(*a):
        raise AssertionError("read the JSON cache again")

    monkeypatch.setattr(Prober, "_read", staticmethod(no_disk))
    assert await prober.indexed(f) == info
    assert await prober.probe(f) == info.__class__(**{**info.__dict__, "keyframes": ()})


def recording_run(calls, info=INFO, keyframes=KEYFRAMES, gate=None):
    async def run(args, timeout=None):
        calls.append((args, timeout))
        if "csv=p=0" in args:
            if gate is not None:
                await gate.wait()
            return keyframes
        return info
    return run


@pytest.mark.anyio
async def test_probe_is_quick_and_index_reads_packets_at_low_priority(tmp_path):
    f = tmp_path / "a.mkv"
    f.write_bytes(b"x")
    calls = []
    prober = Prober(tmp_path / "cache", recording_run(calls))
    info = await prober.probe(f)
    assert info.keyframes == ()
    [(args, timeout)] = calls  # no keyframe scan for the info
    assert "-show_streams" in args and "-show_entries" not in args
    assert timeout <= 30
    await prober.indexed(f)
    args, timeout = calls[1]
    assert args[args.index("-show_entries") + 1] == "packet=pts_time,flags"
    assert args[args.index("-select_streams") + 1] == "V:0"
    assert "-skip_frame" not in args  # nothing is decoded
    assert timeout >= 3600  # a big file on a slow disk still finishes and is cached
    for args, _ in calls:
        assert args[:3] == ["nice", "-n", "10"]
        if shutil.which("ionice"):
            assert args[3:8] == ["ionice", "-c2", "-n7", "-t", "ffprobe"]
        else:
            assert args[3] == "ffprobe"


@pytest.mark.anyio
async def test_index_not_ready_returns_none_and_keeps_building(tmp_path):
    f = tmp_path / "a.mkv"
    f.write_bytes(b"x")
    calls, gate = [], asyncio.Event()
    prober = Prober(tmp_path / "cache", recording_run(calls, gate=gate))
    assert await prober.indexed(f, wait=0.05) is None
    assert await prober.indexed(f, wait=0) is None
    assert sum("csv=p=0" in a for a, _ in calls) == 1  # one scan, still running
    gate.set()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if await prober.indexed(f, wait=0) is not None:
            break
    assert (await prober.indexed(f, wait=0)).keyframes == (0.0, 2.0, 4.0)
    assert sum("csv=p=0" in a for a, _ in calls) == 1
    # the scan finished with nobody waiting, and was still kept on disk
    fresh = []
    assert (await Prober(tmp_path / "cache", recording_run(fresh)).indexed(f, wait=1)) is not None
    assert fresh == []


@pytest.mark.anyio
async def test_one_index_scan_at_a_time_and_info_never_waits_for_one(tmp_path):
    a, b = tmp_path / "a.mkv", tmp_path / "b.mkv"
    a.write_bytes(b"x")
    b.write_bytes(b"y")
    running, most, gate = 0, 0, asyncio.Event()

    async def run(args, timeout=None):
        nonlocal running, most
        if "csv=p=0" not in args:
            return INFO
        running += 1
        most = max(most, running)
        await gate.wait()
        running -= 1
        return KEYFRAMES

    prober = Prober(tmp_path / "cache", run)
    both = asyncio.gather(prober.indexed(a), prober.indexed(b))
    await asyncio.sleep(0.05)
    # a scan is running: the info of a third file still comes at once
    c = tmp_path / "c.mkv"
    c.write_bytes(b"z")
    assert (await asyncio.wait_for(prober.probe(c), 1)).duration == 12.0
    gate.set()
    await both
    assert most == 1


@pytest.mark.anyio
async def test_failed_scan_nobody_waits_for_logs_nothing(tmp_path):
    f = tmp_path / "a.mkv"
    f.write_bytes(b"x")
    seen = []
    asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: seen.append(ctx))
    gate = asyncio.Event()

    async def run(args, timeout=None):
        if "csv=p=0" in args:
            await gate.wait()
            raise MediaError("boom")
        return INFO

    prober = Prober(tmp_path / "cache", run)
    assert await prober.indexed(f, wait=0.01) is None
    gate.set()
    await asyncio.sleep(0.05)
    gc.collect()
    await asyncio.sleep(0)
    assert [c.get("message") for c in seen] == []
    with pytest.raises(MediaError):  # the next request tries again
        await prober.indexed(f, wait=1)


@pytest.mark.anyio
async def test_memory_cache_is_bounded(tmp_path):
    calls = []
    prober = Prober(tmp_path / "cache", fake_run(calls))
    files = []
    for i in range(40):
        f = tmp_path / f"{i}.mkv"
        f.write_bytes(b"x")
        files.append(f)
        await prober.indexed(f)
    assert len(prober._infos) <= 32 and len(prober._indexes) <= 32


@pytest.mark.anyio
async def test_cover_art_is_not_the_video_and_tags_are_kept(tmp_path):
    f = tmp_path / "a.mp4"
    f.write_bytes(b"x")
    info = {"format": {"duration": "12.0", "format_name": "mov,mp4"},
            "streams": [
                {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
                {"codec_type": "video", "codec_name": "hevc", "profile": "Main 10", "level": 120,
                 "codec_tag_string": "hev1", "disposition": {"attached_pic": 0}},
                {"codec_type": "audio", "codec_name": "aac"}]}
    got = await Prober(tmp_path / "cache", fake_run_with(info, KEYFRAMES)).probe(f)
    assert (got.video_codec, got.codec_string, got.codec_tag) == ("hevc", "hvc1.2.4.L120.90", "hev1")
    assert got.profile_known and not got.direct_codec_ok  # Safari won't play hev1 as a file
    info["streams"][1]["codec_tag_string"] = "hvc1"
    got = await Prober(tmp_path / "cache2", fake_run_with(info, KEYFRAMES)).probe(f)
    assert got.direct_codec_ok
    info["streams"][1]["profile"] = "Rext"
    got = await Prober(tmp_path / "cache3", fake_run_with(info, KEYFRAMES)).probe(f)
    assert not got.profile_known and not got.direct_codec_ok
