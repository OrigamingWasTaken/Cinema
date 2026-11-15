"""Probe a media file with ffprobe: duration, codecs, and the keyframe index."""
import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
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
    keyframes: tuple[float, ...]


async def run_tool(args: list[str]) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except OSError as e:
        raise MediaError(f"cannot run {args[0]}: {e}") from e
    out, err = await proc.communicate()
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


class Prober:
    def __init__(self, cache_dir: Path,
                 run: Callable[[list[str]], Awaitable[str]] = run_tool) -> None:
        self.cache_dir = cache_dir
        self._run = run
        self._inflight: dict[str, asyncio.Task[MediaInfo]] = {}

    async def probe(self, path: Path) -> MediaInfo:
        st = path.stat()
        key = hashlib.sha1(f"{path}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()
        cached = self.cache_dir / f"{key}.json"
        try:
            data = json.loads(cached.read_text())
            return MediaInfo(**{**data, "keyframes": tuple(data["keyframes"])})
        except (OSError, ValueError, TypeError, KeyError):
            pass
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.ensure_future(self._probe(path, cached))
            self._inflight[key] = task
            task.add_done_callback(lambda _t: self._inflight.pop(key, None))
        return await asyncio.shield(task)

    async def _probe(self, path: Path, cached: Path) -> MediaInfo:
        info_raw = await self._run(["ffprobe", "-v", "error", "-print_format", "json",
                                    "-show_format", "-show_streams", str(path)])
        kf_raw = await self._run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                  "-skip_frame", "nokey", "-show_entries", "frame=pts_time",
                                  "-of", "csv=p=0", str(path)])
        try:
            data = json.loads(info_raw)
            streams = data.get("streams", [])
            video = next(s for s in streams if s.get("codec_type") == "video")
            audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
            fmt = data.get("format", {})
            frames = sorted({round(float(v), 6) for v in
                             (line.split(",")[0].strip() for line in kf_raw.splitlines())
                             if v and v != "N/A"})
            info = MediaInfo(
                duration=float(fmt.get("duration") or video.get("duration")),
                container=_container(path, str(fmt.get("format_name", ""))),
                video_codec=str(video.get("codec_name")),
                codec_string=codec_string(video),
                audio_codec=audio.get("codec_name") if audio else None,
                keyframes=tuple(frames),
            )
        except (ValueError, TypeError, StopIteration, KeyError) as e:
            raise MediaError(f"unreadable ffprobe output for {path.name}") from e
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cached.write_text(json.dumps(asdict(info)))
        except OSError:
            pass
        return info
