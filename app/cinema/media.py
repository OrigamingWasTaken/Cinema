"""Probe a media file with ffprobe: duration and codecs (quick), and the keyframe index
(reads the whole file, so it is built in the background)."""
import asyncio
import contextlib
import functools
import hashlib
import json
import shutil
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, replace
from pathlib import Path


class MediaError(Exception):
    pass


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    container: str
    video_codec: str
    codec_string: str
    audio_codec: str | None
    # Keyframe times from the file start; empty until Prober.indexed fills them in.
    keyframes: tuple[float, ...] = ()
    # ffprobe's codec_tag_string: the MP4 sample entry (avc1, avc3, hvc1, hev1) or junk.
    codec_tag: str = ""
    # codec_string names the real profile, not a guess.
    profile_known: bool = False
    start: float = 0.0  # the container's start time

    @property
    def direct_codec_ok(self) -> bool:
        """Browsers can be trusted with the file itself: codec_string is exact and the
        sample entry is one they all play (Safari says yes to hvc1 but won't play hev1)."""
        return self.profile_known and self.codec_tag in ("avc1", "hvc1")


@functools.cache
def low_priority() -> list[str]:
    """nice, and the lowest best-effort IO priority where ionice exists (-t: never fail
    over it), so rqbit and WebDAV (Infuse) always come first."""
    cmd = ["nice", "-n", "10"]
    if shutil.which("ionice"):
        cmd += ["ionice", "-c2", "-n7", "-t"]
    return cmd


async def run_tool(args: list[str], timeout: float = 300.0) -> str:
    """Run a tool and return its stdout. The child is killed on timeout or cancellation."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError as e:
        raise MediaError(f"cannot run {args[0]}: {e}") from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException as e:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
        if isinstance(e, TimeoutError):
            raise MediaError(f"{args[0]} timed out after {timeout:g} s") from None
        raise
    if proc.returncode != 0:
        raise MediaError(f"{args[0]} exited {proc.returncode}: {err.decode(errors='replace')[-300:]}")
    return out.decode(errors="replace")


# profile name -> (profile_idc, constraint byte)
_H264 = {
    "baseline": (0x42, 0x00),
    "constrained baseline": (0x42, 0xC0),
    "main": (0x4D, 0x40),
    "extended": (0x58, 0x00),
    "high": (0x64, 0x00),
    "high 10": (0x6E, 0x00),
    "high 4:2:2": (0x7A, 0x00),
    "high 4:4:4 predictive": (0xF4, 0x00),
}
# profile name -> (general_profile_idc, compatibility flags)
_HEVC = {"main": (1, "6"), "main 10": (2, "4")}


def profile_known(stream: dict) -> bool:
    profile = str(stream.get("profile", "")).lower()
    return profile in {"h264": _H264, "hevc": _HEVC}.get(str(stream.get("codec_name")), {})


def codec_string(stream: dict) -> str:
    """The RFC 6381 codec string for an ffprobe video stream."""
    codec = stream.get("codec_name")
    profile = str(stream.get("profile", "")).lower()
    level = int(stream.get("level") or 0)
    if codec == "h264":
        idc, constraint = _H264.get(profile, (0x64, 0x00))
        return f"avc1.{idc:02x}{constraint:02x}{level:02x}"
    if codec == "hevc":
        idc, compat = _HEVC.get(profile, (1, "6"))
        return f"hvc1.{idc}.{compat}.L{level}.90"
    return str(codec)


def _container(path: Path, format_name: str) -> str:
    ext = path.suffix.lower()
    if ext in (".mp4", ".m4v"):
        return "mp4"
    if ext == ".mkv":
        return "mkv"
    names = format_name.split(",")
    if "matroska" in names:
        return "mkv"
    if "mp4" in names or "mov" in names:
        return "mp4"
    return names[0]


# Bump when a cached file changes meaning (3: info and keyframe index cached apart).
_CACHE_VERSION = 3
INFO_TIMEOUT_S = 30.0  # headers only; the info request must answer well inside a proxy timeout
INDEX_TIMEOUT_S = 4 * 3600.0  # reads every packet of the file, niced, one file at a time
MEMORY_ENTRIES = 32


def _parse_info(path: Path, raw: str) -> MediaInfo:
    try:
        data = json.loads(raw)
        streams = data.get("streams", [])
        # Cover art is a one-frame "video" stream; ffmpeg's V (capital) skips it too.
        video = next(s for s in streams if s.get("codec_type") == "video"
                     and not (s.get("disposition") or {}).get("attached_pic"))
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        fmt = data.get("format", {})
        start = fmt.get("start_time")
        return MediaInfo(
            duration=float(fmt.get("duration") or video.get("duration")),
            container=_container(path, str(fmt.get("format_name", ""))),
            video_codec=str(video.get("codec_name")),
            codec_string=codec_string(video),
            audio_codec=audio.get("codec_name") if audio else None,
            codec_tag=str(video.get("codec_tag_string", "")),
            profile_known=profile_known(video),
            start=float(start) if start not in (None, "N/A") else 0.0,
        )
    except (ValueError, TypeError, StopIteration, KeyError, AttributeError) as e:
        raise MediaError(f"unreadable ffprobe output for {path.name}") from e


def _keyframes(raw: str, start: float) -> tuple[float, ...]:
    """Keyframe times relative to the file start, as ffmpeg -ss and -copyts -start_at_zero
    count them, from `pts_time,flags` packet lines (K marks a keyframe). Negative times
    are dropped and the list always begins at exactly 0.0."""
    times = set()
    for line in raw.splitlines():
        pts, _, flags = line.partition(",")
        pts = pts.strip()
        if "K" in flags and pts and pts != "N/A":
            times.add(round(float(pts) - start, 6) + 0.0)
    frames = sorted(t for t in times if t >= 0)
    if not frames or frames[0] != 0.0:
        frames.insert(0, 0.0)
    return tuple(frames)


class _Lru(OrderedDict):
    def __init__(self, size: int) -> None:
        super().__init__()
        self.size = size

    def get(self, key, default=None):
        if key in self:
            self.move_to_end(key)
            return self[key]
        return default

    def put(self, key, value) -> None:
        self[key] = value
        self.move_to_end(key)
        while len(self) > self.size:
            self.popitem(last=False)


class Prober:
    """ffprobe results, kept in memory (the last MEMORY_ENTRIES files) and on disk.

    probe() reads only the headers: it answers in seconds and is all the info request
    and direct play need. indexed() adds the keyframe index, which reads every packet
    of the file (no decoding); that runs in the background, one file at a time, and
    goes on when nobody waits for it any more, so a slow first scan is still kept.
    """

    def __init__(self, cache_dir: Path,
                 run: Callable[..., Awaitable[str]] = run_tool) -> None:
        self.cache_dir = cache_dir
        self._run = run
        self._inflight: dict[tuple[str, str], asyncio.Task] = {}
        self._infos: _Lru = _Lru(MEMORY_ENTRIES)
        self._indexes: _Lru = _Lru(MEMORY_ENTRIES)
        # One of each at a time: an info probe never queues behind a long index scan.
        self._info_slot = asyncio.Semaphore(1)
        self._index_slot = asyncio.Semaphore(1)

    async def probe(self, path: Path) -> MediaInfo:
        key = await self._key(path)
        return await self._get("info", key, path, None)

    async def indexed(self, path: Path, wait: float | None = None) -> MediaInfo | None:
        """probe() with the keyframes, or None when the index is not ready within `wait`
        seconds (None: wait as long as it takes). The scan carries on either way."""
        key = await self._key(path)
        info = await self._get("info", key, path, None)
        frames = await self._get("index", key, path, wait)
        return None if frames is None else replace(info, keyframes=frames)

    async def _key(self, path: Path) -> str:
        try:
            st = await asyncio.to_thread(path.stat)
        except OSError as e:
            raise MediaError(f"cannot read {path.name}: {e.strerror}") from e
        return hashlib.sha1(
            f"{_CACHE_VERSION}|{path}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()

    async def _get(self, kind: str, key: str, path: Path, wait: float | None):
        memory = self._infos if kind == "info" else self._indexes
        known = memory.get(key)
        if known is not None:
            return known
        task = self._inflight.get((kind, key))
        if task is None:
            task = asyncio.ensure_future(self._load(kind, key, path))
            self._inflight[(kind, key)] = task
            task.add_done_callback(lambda t: self._finished((kind, key), t))
        try:
            return await asyncio.wait_for(asyncio.shield(task), wait)
        except TimeoutError:
            return None

    def _finished(self, which: tuple[str, str], task: asyncio.Task) -> None:
        self._inflight.pop(which, None)
        if not task.cancelled():
            task.exception()  # retrieved: a scan nobody waits for any more fails quietly

    async def _load(self, kind: str, key: str, path: Path):
        cached = self.cache_dir / (f"{key}.json" if kind == "info" else f"{key}.keyframes.json")
        value = await asyncio.to_thread(self._read, kind, cached)
        if value is None:
            if kind == "info":
                async with self._info_slot:
                    raw = await self._run([*low_priority(), "ffprobe", "-v", "error",
                                           "-print_format", "json", "-show_format",
                                           "-show_streams", str(path)], timeout=INFO_TIMEOUT_S)
                value = _parse_info(path, raw)
                data = asdict(value)
                del data["keyframes"]
            else:
                info = await self._get("info", key, path, None)
                async with self._index_slot:
                    raw = await self._run([*low_priority(), "ffprobe", "-v", "error",
                                           "-select_streams", "V:0",
                                           "-show_entries", "packet=pts_time,flags",
                                           "-of", "csv=p=0", str(path)], timeout=INDEX_TIMEOUT_S)
                try:
                    value = _keyframes(raw, info.start)
                except ValueError as e:
                    raise MediaError(f"unreadable ffprobe output for {path.name}") from e
                data = list(value)
            await asyncio.to_thread(self._write, cached, data)
        (self._infos if kind == "info" else self._indexes).put(key, value)
        return value

    @staticmethod
    def _read(kind: str, cached: Path):
        try:
            data = json.loads(cached.read_text())
            if kind == "info":
                return MediaInfo(**data)
            return tuple(float(t) for t in data)
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def _write(self, cached: Path, data) -> None:
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(data))
        except OSError:
            pass
