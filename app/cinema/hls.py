"""HLS for the browser player: keyframe-aligned VOD playlists, and ffmpeg sessions that
repackage a file into fMP4 segments (video copied, audio to AAC stereo), capped in number."""
import asyncio
import bisect
import logging
import math
import os
import shutil
import signal
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .media import MediaError, MediaInfo

log = logging.getLogger(__name__)

Segment = tuple[float, float]  # (start, length) in seconds from the file start

LOOKAHEAD = 3       # a request this many segments past the newest written one waits
MAX_AHEAD = 5       # ffmpeg pauses once it has written this far past the last request
KEEP_BEHIND = 10    # segment files further behind the last request are deleted
PACE_S = 0.5        # how often run_reaper checks how far ahead ffmpeg is
SEEK_MARGIN = 0.2   # see FfmpegRunner.command
AUDIO_START = 0.1   # see FfmpegRunner.command
MOVFLAGS = "+frag_custom+empty_moov+default_base_moof+frag_discont+negative_cts_offsets+skip_trailer"


def plan_segments(keyframes: tuple[float, ...], duration: float,
                  target: float = 6.0) -> list[Segment]:
    """Cut on keyframes: a segment starts at the first keyframe at least `target` seconds
    after the previous start; the last one runs to the end."""
    starts: list[float] = []
    for kf in keyframes:
        if kf >= duration:
            break
        if not starts or kf >= starts[-1] + target:
            starts.append(kf)
    if not starts:
        starts = [0.0]
    ends = starts[1:] + [duration]
    return [(s, e - s) for s, e in zip(starts, ends)]


def render_playlist(segments: list[Segment], *, init_uri: str,
                    seg_uri: Callable[[int], str]) -> str:
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:7",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        "#EXT-X-INDEPENDENT-SEGMENTS",
        f"#EXT-X-TARGETDURATION:{math.ceil(round(max(n for _, n in segments), 3))}",
        f'#EXT-X-MAP:URI="{init_uri}"',
    ]
    for n, (_, length) in enumerate(segments):
        lines += [f"#EXTINF:{length:.3f},", seg_uri(n)]
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


class BusyError(Exception):
    pass


class Proc(Protocol):
    def done(self) -> bool: ...

    def kill(self) -> None: ...

    def pause(self) -> None: ...

    def resume(self) -> None: ...


class Runner(Protocol):
    def start(self, path: Path, info: MediaInfo, segments: list[Segment], first: int,
              out_dir: Path) -> Proc: ...


class _Process:
    def __init__(self, popen: subprocess.Popen) -> None:
        self._popen = popen

    def done(self) -> bool:
        return self._popen.poll() is not None

    def pause(self) -> None:
        self._signal(signal.SIGSTOP)

    def resume(self) -> None:
        self._signal(signal.SIGCONT)

    def _signal(self, sig: int) -> None:
        if self._popen.poll() is None:
            try:
                self._popen.send_signal(sig)
            except ProcessLookupError:
                pass

    def kill(self) -> None:
        if self._popen.poll() is None:
            self._popen.kill()  # SIGKILL also ends a stopped process
            self._signal(signal.SIGCONT)
        try:
            self._popen.wait(timeout=5)
        except subprocess.TimeoutExpired:
            log.warning("ffmpeg %s did not exit after SIGKILL", self._popen.pid)


class FfmpegRunner:
    """Writes `out_dir/init.mp4` and `out_dir/{n}.m4s` from segment `first` on.

    Timeline: `-copyts -start_at_zero` keeps the source timestamps, counted from the file
    start like MediaInfo.keyframes, so a restart's segments line up with earlier ones.
    The segment muxer with one mp4 muxer behind it writes the moov once (init.mp4,
    identical for every restart) and one moof+mdat run per segment. `frag_discont` makes
    each segment's tfdt the absolute decode time; `negative_cts_offsets` sets decode time
    = presentation time on the first keyframe, so B-frame delay never makes it negative.
    """

    def command(self, path: Path, info: MediaInfo, segments: list[Segment], first: int,
                out_dir: Path) -> list[str]:
        kfs = info.keyframes
        start = segments[first][0]
        # With B-frames, ffmpeg seeks to 3/23 s before -ss for some demuxers (MKV) and
        # copies from the keyframe it lands on. Aim between this keyframe and the next
        # so the landing keyframe is this one either way.
        i = bisect.bisect_right(kfs, start)
        after = kfs[i] if i < len(kfs) else start + segments[first][1]
        seek = start + min(SEEK_MARGIN, (after - start) / 2)
        # The segment muxer cuts at the first keyframe at or after each time, counted from
        # the first video packet. Halfway between a planned start and the keyframe before
        # it tolerates rounding and lands the cut exactly on the planned keyframe.
        cuts = []
        for s, _ in segments[first + 1:]:
            j = bisect.bisect_left(kfs, s)
            before = max(kfs[j - 1] if j > 0 else start, start)
            cuts.append((before + s) / 2 - start)
        times = ",".join(f"{t:.6f}" for t in cuts or [1e9])

        # Low CPU priority, and idle IO priority where ionice exists, so rqbit and
        # WebDAV (Infuse) always come first.
        cmd = ["nice", "-n", "10"]
        if shutil.which("ionice"):
            cmd += ["ionice", "-c3"]
        cmd += ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
               "-noaccurate_seek", "-ss", f"{seek:.6f}", "-copyts", "-start_at_zero",
               "-i", f"file:{path}", "-map", "0:v:0"]
        if info.audio_codec:
            cmd += ["-map", "0:a:0"]
        cmd += ["-map_metadata", "-1", "-map_chapters", "-1", "-c:v", "copy"]
        if info.video_codec == "hevc":
            cmd += ["-tag:v", "hvc1"]  # Safari plays HEVC only as hvc1
        if info.audio_codec:
            # AAC priming puts the first packet 1024 samples before its input; at the
            # file start that would be a negative time, which fMP4 cannot hold.
            cmd += ["-af", f"atrim=start={AUDIO_START}",
                    "-c:a", "aac", "-ac", "2", "-b:a", "192k"]
        cmd += ["-avoid_negative_ts", "disabled",
                "-f", "segment", "-segment_format", "mp4",
                "-segment_format_options",
                f"movflags={MOVFLAGS}:use_editlist=0:avoid_negative_ts=disabled",
                "-segment_header_filename", str(out_dir / "init.mp4"),
                "-segment_times", times,
                "-segment_start_number", str(first),
                str(out_dir / "%d.m4s")]
        return cmd

    def start(self, path: Path, info: MediaInfo, segments: list[Segment], first: int,
              out_dir: Path) -> Proc:
        out_dir.mkdir(parents=True, exist_ok=True)
        cmd = self.command(path, info, segments, first, out_dir)
        try:
            popen = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
        except OSError as e:
            raise MediaError(f"cannot run ffmpeg: {e}") from e
        return _Process(popen)


@dataclass
class _Run:
    first: int
    dir: Path
    proc: Proc
    newest: int  # highest segment file seen so far
    kept_from: int  # files below this index have been deleted
    paused: bool = False


@dataclass
class _Session:
    key: tuple[str, str]
    dir: Path
    segments: list[Segment]
    last_used: float
    run: _Run | None = None
    runs: int = 0
    requested: int = 0  # the last segment the client asked for


class StreamSessions:
    """One ffmpeg per (device_id, item_id), at most `max_sessions` at once.

    Each ffmpeg run writes into its own directory; a restart (seek) kills the run and
    deletes its directory, so a file is never served while a newer run rewrites it.
    ffmpeg is paused (SIGSTOP) once it is MAX_AHEAD segments past the last request and
    resumed when playback catches up; files more than KEEP_BEHIND behind are deleted.
    """

    def __init__(self, *, tmp_root: Path, runner: Runner = FfmpegRunner(),
                 max_sessions: int = 2, idle_s: float = 60.0,
                 now: Callable[[], float] = time.monotonic, wait_s: float = 30.0) -> None:
        self.tmp_root = tmp_root
        self.runner = runner
        self.max_sessions = max_sessions
        self.idle_s = idle_s
        self.wait_s = wait_s
        self._now = now
        self._sessions: dict[tuple[str, str], _Session] = {}

    async def init(self, key: tuple[str, str], path: Path, info: MediaInfo) -> Path:
        s = self._session(key, info)
        kept = s.dir / "init.mp4"
        if kept.exists():
            return kept
        if s.run is None:
            self._start(s, path, info, 0)
        run = s.run
        made, first = run.dir / "init.mp4", run.dir / f"{run.first}.m4s"
        await self._wait(s, run, lambda ended: made.exists() and (ended or first.exists()),
                         "the init segment")
        # Keep the first run's init: every run writes the same one, and a restart
        # deletes its run directory.
        try:
            shutil.copyfile(made, s.dir / "init.mp4.part")
            os.replace(s.dir / "init.mp4.part", kept)
        except OSError as e:
            raise MediaError(f"cannot keep the init segment: {e}") from e
        self._pace(s)
        s.last_used = self._now()
        return kept

    async def segment(self, key: tuple[str, str], path: Path, info: MediaInfo,
                      n: int) -> Path:
        s = self._session(key, info)
        if not 0 <= n < len(s.segments):
            raise IndexError(f"no segment {n}")
        run = s.run
        s.requested = n
        if run is None or n < run.kept_from or n > self._newest(run) + LOOKAHEAD:
            run = self._start(s, path, info, n)
        self._trim(run, n)
        self._pace(s)
        f, after = run.dir / f"{n}.m4s", run.dir / f"{n + 1}.m4s"
        # A segment is complete once ffmpeg has opened the next one, or has ended.
        await self._wait(s, run, lambda ended: f.exists() and (ended or after.exists()),
                         f"segment {n}")
        s.last_used = self._now()
        return f

    def reap(self) -> None:
        now = self._now()
        for s in list(self._sessions.values()):
            if now - s.last_used >= self.idle_s:
                log.info("closing idle stream session %s", s.key)
                self._close(s)
            else:
                self._pace(s)

    async def close_all(self) -> None:
        for s in list(self._sessions.values()):
            self._close(s)

    async def run_reaper(self, interval: float = 10.0) -> None:
        """Reap idle sessions every `interval`; between those, keep ffmpeg paced."""
        last = time.monotonic()
        while True:
            await asyncio.sleep(min(PACE_S, interval))
            try:
                if time.monotonic() - last >= interval:
                    last = time.monotonic()
                    self.reap()
                else:
                    for s in list(self._sessions.values()):
                        self._pace(s)
            except Exception:
                log.exception("stream session reaper failed")

    def _session(self, key: tuple[str, str], info: MediaInfo) -> _Session:
        s = self._sessions.get(key)
        if s is None:
            self.reap()
            for other in list(self._sessions.values()):
                if other.key[0] == key[0]:
                    self._close(other)
            if len(self._sessions) >= self.max_sessions:
                raise BusyError(f"{len(self._sessions)} streams already running")
            d = self.tmp_root / uuid.uuid4().hex
            d.mkdir(parents=True)
            s = _Session(key, d, plan_segments(info.keyframes, info.duration), self._now())
            self._sessions[key] = s
        s.last_used = self._now()
        return s

    def _start(self, s: _Session, path: Path, info: MediaInfo, first: int) -> _Run:
        if s.run is not None:
            self._stop(s.run)
            s.run = None
        s.runs += 1
        out = s.dir / f"run{s.runs}"
        proc = self.runner.start(path, info, s.segments, first, out)
        s.run = _Run(first, out, proc, first - 1, first)
        return s.run

    @staticmethod
    def _stop(run: _Run) -> None:
        run.proc.kill()
        shutil.rmtree(run.dir, ignore_errors=True)

    def _close(self, s: _Session) -> None:
        self._sessions.pop(s.key, None)
        if s.run is not None:
            self._stop(s.run)
            s.run = None
        shutil.rmtree(s.dir, ignore_errors=True)

    def _pace(self, s: _Session) -> None:
        """Pause ffmpeg once it is MAX_AHEAD segments past the last request, so converted
        segments don't pile up and the disk stays free for other readers."""
        run = s.run
        if run is None or run.proc.done():
            return
        far = self._newest(run) >= max(s.requested, run.first) + MAX_AHEAD
        if far and not run.paused:
            run.proc.pause()
            run.paused = True
        elif not far and run.paused:
            run.proc.resume()
            run.paused = False

    @staticmethod
    def _trim(run: _Run, requested: int) -> None:
        """Delete segment files more than KEEP_BEHIND behind the last request."""
        while run.kept_from < requested - KEEP_BEHIND:
            (run.dir / f"{run.kept_from}.m4s").unlink(missing_ok=True)
            run.kept_from += 1

    @staticmethod
    def _newest(run: _Run) -> int:
        while (run.dir / f"{run.newest + 1}.m4s").exists():
            run.newest += 1
        return run.newest

    async def _wait(self, s: _Session, run: _Run, ready: Callable[[bool], bool],
                    what: str) -> None:
        deadline = time.monotonic() + self.wait_s
        while True:
            if s.run is not run:
                raise MediaError(f"{what}: the stream was restarted or closed")
            ended = run.proc.done()  # read before the files, so a final write is seen
            if ready(ended):
                return
            if run.paused:  # another request moved back meanwhile; this one needs more
                run.proc.resume()
                run.paused = False
            if ended:
                raise MediaError(f"ffmpeg ended without writing {what}")
            if time.monotonic() >= deadline:
                raise MediaError(f"timed out waiting for {what}")
            await asyncio.sleep(0.05)
