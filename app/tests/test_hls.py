import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cinema.hls import (BusyError, FfmpegRunner, StreamSessions, _Process, plan_segments,
                        render_playlist)
from cinema.media import MediaError, MediaInfo, Prober


def test_plan_segments_on_keyframes():
    kfs = tuple(float(t) for t in range(0, 21, 2))
    assert plan_segments(kfs, 21.0, 6.0) == [(0, 6), (6, 6), (12, 6), (18, 3)]


def test_plan_segments_irregular_keyframes():
    assert plan_segments((0.0, 5.0, 7.0, 13.0, 14.0), 20.0) == [(0, 7), (7, 6), (13, 7)]


def test_render_playlist_vod():
    text = render_playlist([(0.0, 6.006), (6.006, 5.5), (11.506, 2.0)],
                           init_uri="init.mp4?d=x", seg_uri=lambda n: f"seg/{n}.m4s?d=x")
    assert text.splitlines() == [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-INDEPENDENT-SEGMENTS",
        "#EXT-X-TARGETDURATION:7",
        '#EXT-X-MAP:URI="init.mp4?d=x"',
        "#EXTINF:6.006,",
        "seg/0.m4s?d=x",
        "#EXTINF:5.500,",
        "seg/1.m4s?d=x",
        "#EXTINF:2.000,",
        "seg/2.m4s?d=x",
        "#EXT-X-ENDLIST",
    ]


# 300 s, a keyframe every 2 s: 50 segments of 6 s.
INFO = MediaInfo(300.0, "mkv", "h264", "avc1.640028", "ac3",
                 tuple(float(t) for t in range(0, 300, 2)))


class FakeProc:
    def __init__(self):
        self.killed = False
        self.finished = False
        self.paused = False
        self.pauses = 0

    def done(self):
        return self.finished or self.killed

    def kill(self):
        self.killed = True

    def pause(self):
        self.paused = True
        self.pauses += 1

    def resume(self):
        self.paused = False


class FakeRunner:
    """Writes init.mp4 and the next `ahead` segments at once, as a fast ffmpeg would."""

    def __init__(self, ahead=5):
        self.ahead = ahead
        self.starts = []
        self.procs = []

    def start(self, path, info, segments, first, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "init.mp4").write_bytes(b"init")
        for n in range(first, min(first + self.ahead, len(segments))):
            (out_dir / f"{n}.m4s").write_bytes(f"seg{n}".encode())
        self.starts.append(first)
        self.procs.append(FakeProc())
        return self.procs[-1]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def sessions_for(tmp_path, runner, clock=None, **kw):
    return StreamSessions(tmp_root=tmp_path / "hls", runner=runner,
                          now=clock or Clock(), **kw)


@pytest.mark.anyio
async def test_serves_init_and_complete_segments(tmp_path):
    runner = FakeRunner()
    s = sessions_for(tmp_path, runner)
    init = await s.init(("d1", "m1"), tmp_path / "f.mkv", INFO)
    assert init.read_bytes() == b"init"
    seg = await s.segment(("d1", "m1"), tmp_path / "f.mkv", INFO, 0)
    assert seg.read_bytes() == b"seg0"
    assert runner.starts == [0]


@pytest.mark.anyio
async def test_third_session_busy(tmp_path):
    s = sessions_for(tmp_path, FakeRunner())
    await s.segment(("d1", "m1"), tmp_path / "f.mkv", INFO, 0)
    await s.segment(("d2", "m1"), tmp_path / "f.mkv", INFO, 0)
    with pytest.raises(BusyError):
        await s.segment(("d3", "m2"), tmp_path / "g.mkv", INFO, 0)
    with pytest.raises(BusyError):
        await s.init(("d3", "m2"), tmp_path / "g.mkv", INFO)


@pytest.mark.anyio
async def test_same_device_replaces_session(tmp_path):
    runner = FakeRunner()
    s = sessions_for(tmp_path, runner)
    await s.segment(("d1", "m1"), tmp_path / "f.mkv", INFO, 0)
    await s.segment(("d2", "m1"), tmp_path / "f.mkv", INFO, 0)
    old_dirs = set((tmp_path / "hls").iterdir())
    await s.segment(("d1", "m2"), tmp_path / "g.mkv", INFO, 0)
    assert runner.procs[0].killed and not runner.procs[1].killed
    assert len(set((tmp_path / "hls").iterdir()) - old_dirs) == 1
    assert len(list((tmp_path / "hls").iterdir())) == 2
    # the same device and item keeps its session
    await s.segment(("d1", "m2"), tmp_path / "g.mkv", INFO, 1)
    assert runner.starts == [0, 0, 0]


@pytest.mark.anyio
async def test_idle_session_killed_and_cleaned(tmp_path):
    runner = FakeRunner()
    clock = Clock()
    s = sessions_for(tmp_path, runner, clock)
    await s.segment(("d1", "m1"), tmp_path / "f.mkv", INFO, 0)
    await s.segment(("d2", "m1"), tmp_path / "f.mkv", INFO, 0)
    clock.t += 30
    await s.segment(("d2", "m1"), tmp_path / "f.mkv", INFO, 1)
    clock.t += 30
    s.reap()
    assert runner.procs[0].killed and not runner.procs[1].killed
    assert len(list((tmp_path / "hls").iterdir())) == 1
    # the freed slot can be taken again
    await s.segment(("d3", "m2"), tmp_path / "g.mkv", INFO, 0)


@pytest.mark.anyio
async def test_close_all_kills_everything(tmp_path):
    runner = FakeRunner()
    s = sessions_for(tmp_path, runner)
    await s.segment(("d1", "m1"), tmp_path / "f.mkv", INFO, 0)
    await s.segment(("d2", "m1"), tmp_path / "f.mkv", INFO, 0)
    await s.close_all()
    assert all(p.killed for p in runner.procs)
    assert list((tmp_path / "hls").iterdir()) == []


@pytest.mark.anyio
async def test_seek_restarts_at_segment(tmp_path):
    runner = FakeRunner()
    s = sessions_for(tmp_path, runner)
    key, f = ("d1", "m1"), tmp_path / "f.mkv"
    assert (await s.segment(key, f, INFO, 0)).read_bytes() == b"seg0"
    assert (await s.segment(key, f, INFO, 40)).read_bytes() == b"seg40"
    assert runner.starts == [0, 40]
    assert runner.procs[0].killed
    assert (await s.segment(key, f, INFO, 41)).read_bytes() == b"seg41"
    assert runner.starts == [0, 40]
    # behind the running process: restart there
    assert (await s.segment(key, f, INFO, 39)).read_bytes() == b"seg39"
    assert runner.starts == [0, 40, 39]
    # within 3 of the newest written segment (43): no restart, even if ffmpeg ended
    runner.procs[-1].finished = True
    with pytest.raises(MediaError):
        await s.segment(key, f, INFO, 46)
    assert runner.starts == [0, 40, 39]


@pytest.mark.anyio
async def test_segment_waits_until_the_next_one_exists(tmp_path):
    runner = FakeRunner(ahead=1)
    s = sessions_for(tmp_path, runner, wait_s=0.3)
    key, f = ("d1", "m1"), tmp_path / "f.mkv"
    with pytest.raises(MediaError):  # 0.m4s exists but may still be written
        await s.segment(key, f, INFO, 0)
    runner.procs[0].finished = True  # ffmpeg ended: what is there is complete
    assert (await s.segment(key, f, INFO, 0)).read_bytes() == b"seg0"


@pytest.mark.anyio
async def test_ended_process_without_the_segment_is_media_error(tmp_path):
    runner = FakeRunner(ahead=1)
    s = sessions_for(tmp_path, runner)
    await s.init(("d1", "m1"), tmp_path / "f.mkv", INFO)
    runner.procs[0].finished = True
    with pytest.raises(MediaError):
        await s.segment(("d1", "m1"), tmp_path / "f.mkv", INFO, 2)


@pytest.mark.anyio
async def test_ffmpeg_paused_when_far_ahead_and_resumed(tmp_path):
    runner = FakeRunner(ahead=10)  # writes 0..9 at once
    s = sessions_for(tmp_path, runner)
    key, f = ("d1", "m1"), tmp_path / "f.mkv"
    await s.segment(key, f, INFO, 0)
    proc = runner.procs[0]
    assert proc.paused  # newest written 9 >= requested 0 + 5
    await s.segment(key, f, INFO, 4)
    assert proc.paused  # 9 >= 4 + 5
    await s.segment(key, f, INFO, 5)
    assert not proc.paused  # 9 < 5 + 5: let ffmpeg write on
    # the reaper's check pauses it again once it is far enough ahead
    for n in range(10, 12):
        (s._sessions[key].run.dir / f"{n}.m4s").write_bytes(b"x")
    s.reap()
    assert proc.paused and proc.pauses == 2
    # a restart kills the paused process and starts a fresh, running one
    runner.ahead = 3
    await s.segment(key, f, INFO, 40)
    assert proc.killed
    assert runner.procs[1].pauses == 0


@pytest.mark.anyio
async def test_old_segments_deleted(tmp_path):
    runner = FakeRunner(ahead=30)
    s = sessions_for(tmp_path, runner)
    key, f = ("d1", "m1"), tmp_path / "f.mkv"
    await s.init(key, f, INFO)
    for n in range(0, 16):
        await s.segment(key, f, INFO, n)
    run_dir = s._sessions[key].run.dir
    left = sorted(int(p.stem) for p in run_dir.glob("*.m4s"))
    assert left[0] == 5  # 15 - 10: older ones are gone
    assert (s._sessions[key].dir / "init.mp4").exists()
    # a deleted segment is made again by a restart, not waited for
    assert (await s.segment(key, f, INFO, 2)).read_bytes() == b"seg2"
    assert runner.starts == [0, 2]


@pytest.mark.anyio
async def test_paused_session_reaped_cleanly(tmp_path):
    runner = FakeRunner(ahead=10)
    clock = Clock()
    s = sessions_for(tmp_path, runner, clock)
    await s.segment(("d1", "m1"), tmp_path / "f.mkv", INFO, 0)
    assert runner.procs[0].paused
    clock.t += 60
    s.reap()
    assert runner.procs[0].killed
    assert list((tmp_path / "hls").iterdir()) == []


@pytest.mark.skipif(shutil.which("sleep") is None, reason="no sleep")
def test_paused_process_can_be_killed():
    proc = _Process(subprocess.Popen(["sleep", "30"]))
    proc.pause()
    assert not proc.done()
    proc.resume()
    proc.pause()
    proc.kill()
    assert proc.done()


def test_ffmpeg_command_shape(tmp_path):
    info = MediaInfo(30.0, "mkv", "hevc", "hvc1.1.6.L93.90", "ac3",
                     (0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0))
    segs = plan_segments(info.keyframes, info.duration)
    cmd = FfmpegRunner().command(Path("/m/f.mkv"), info, segs, 1, tmp_path)
    assert cmd[:3] == ["nice", "-n", "10"]
    if shutil.which("ionice"):
        assert cmd[3:6] == ["ionice", "-c3", "ffmpeg"]
    else:
        assert cmd[3] == "ffmpeg"
    i = cmd.index("-i")
    assert cmd[i + 1] == "file:/m/f.mkv"
    ss = cmd.index("-ss")
    assert ss < i and 6.0 < float(cmd[ss + 1]) < 8.0
    assert cmd[cmd.index("-c:v") + 1] == "copy"
    assert cmd[cmd.index("-c:a") + 1] == "aac"
    assert cmd[cmd.index("-ac") + 1] == "2"
    assert cmd[cmd.index("-b:a") + 1] == "192k"
    assert cmd[cmd.index("-tag:v") + 1] == "hvc1"
    assert cmd[cmd.index("-segment_start_number") + 1] == "1"
    assert cmd[-1] == str(tmp_path / "%d.m4s")
    assert cmd[cmd.index("-segment_header_filename") + 1] == str(tmp_path / "init.mp4")
    # one cut, between the keyframes at 10 and 12, relative to the start at 6
    assert [float(t) for t in cmd[cmd.index("-segment_times") + 1].split(",")] == [5.0]


def _first_packet(init: Path, seg: Path, tmp_path: Path, stream: str) -> dict:
    joined = tmp_path / "joined.mp4"
    joined.write_bytes(init.read_bytes() + seg.read_bytes())
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries",
         "packet=pts_time,flags:stream=codec_name,channels", "-read_intervals", "%+#1",
         "-of", "json", str(joined)], check=True, capture_output=True, text=True).stdout
    data = json.loads(out)
    return {**data["streams"][0], **data["packets"][0]}


@pytest.mark.anyio
@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
                    reason="ffmpeg not installed")
async def test_real_ffmpeg_segment_times_match_playlist(tmp_path):
    f = tmp_path / "sample.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=24000/1001",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "30",
         "-c:v", "libx264", "-bf", "3", "-x264-params", "b-adapt=0",
         "-force_key_frames", "expr:gte(t,n_forced*2)", "-sc_threshold", "0",
         "-c:a", "ac3", "-ac", "6", str(f)], check=True)
    info = await Prober(tmp_path / "cache").probe(f)
    segs = plan_segments(info.keyframes, info.duration)
    assert len(segs) == 5

    class Spy(FfmpegRunner):
        def __init__(self):
            self.starts = []

        def start(self, path, info, segments, first, out_dir):
            self.starts.append(first)
            return super().start(path, info, segments, first, out_dir)

    spy = Spy()
    s = StreamSessions(tmp_root=tmp_path / "hls", runner=spy)
    key = ("dev", "item")
    try:
        got = {}
        # Seek straight to the last segment, then back to the start (a restart).
        got[4] = (await s.segment(key, f, info, 4)).read_bytes()
        init = tmp_path / "init.mp4"
        init.write_bytes((await s.init(key, f, info)).read_bytes())
        for n in range(4):
            got[n] = (await s.segment(key, f, info, n)).read_bytes()
        assert spy.starts == [4, 0]
    finally:
        await s.close_all()

    for n, data in got.items():
        seg = tmp_path / f"{n}.m4s"
        seg.write_bytes(data)
        video = _first_packet(init, seg, tmp_path, "v:0")
        assert video["flags"].startswith("K")
        # ffprobe adds the B-frame reorder delay (here 2 frames, 0.083 s) to every pts
        # when composition offsets are negative; players use tfdt + offset, exact here.
        assert float(video["pts_time"]) == pytest.approx(segs[n][0], abs=0.1), n
        audio = _first_packet(init, seg, tmp_path, "a:0")
        assert audio["codec_name"] == "aac" and audio["channels"] == 2
        assert float(audio["pts_time"]) == pytest.approx(segs[n][0], abs=0.2), n

    # The one init with segments from both runs plays through without errors.
    whole = tmp_path / "whole.mp4"
    whole.write_bytes(init.read_bytes() + b"".join(got[n] for n in range(5)))
    decoded = subprocess.run(["ffmpeg", "-v", "error", "-i", str(whole), "-f", "null", "-"],
                             capture_output=True, text=True)
    assert decoded.returncode == 0 and decoded.stderr == ""
