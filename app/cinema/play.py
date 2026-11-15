"""Play finished films and episodes in the browser: file info, the file itself (mp4 only,
with Range) and an HLS rendition of everything else."""
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from fastapi import APIRouter, Query
from starlette.responses import FileResponse, JSONResponse, Response

from cinema.api import NotFoundError, UpstreamError
from cinema.hls import BusyError, StreamSessions, plan_segments, render_playlist
from cinema.media import MediaError, Prober
from cinema.shows import ShowsTracker
from cinema.store import Store

log = logging.getLogger(__name__)

DEVICE_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
DIRECT_EXTENSIONS = {".mp4", ".m4v"}


@dataclass(frozen=True)
class Item:
    item_id: str
    path: Path
    title: str
    subtitle: str | None


def build_play_router(store: Store, tracker: ShowsTracker, prober: Prober,
                      sessions: StreamSessions) -> APIRouter:
    router = APIRouter()

    def movie(tmdb_id: int) -> Item:
        m = store.get(tmdb_id)
        if m is None or m.status != "done":
            raise NotFoundError()
        path = Path(m.file_path)
        if not path.is_file():
            raise NotFoundError()
        return Item(f"movie-{tmdb_id}", path, m.title, None)

    def resolve_episode(show_id: int, season: int, number: int) -> Item:
        for e in tracker.entries:
            if (e.show_tmdb_id == show_id and e.season == season and e.state == "done"
                    and e.episode <= number <= e.last_episode):
                if e.path is None or not e.path.is_file():
                    break
                return Item(f"ep-{show_id}-{season}-{number}", e.path, e.title,
                            f"S{season:02d}E{number:02d}")
        raise NotFoundError()

    def device(d: str | None) -> str:
        if d is None or not DEVICE_RE.match(d):
            return ""
        return d

    async def probe(item: Item):
        try:
            return await prober.probe(item.path)
        except (MediaError, OSError) as exc:
            log.warning("probe failed: %s", exc)
            raise UpstreamError("player") from exc

    async def stream(call):
        try:
            return await call
        except BusyError:
            return JSONResponse({"error": "busy"}, status_code=429)
        except IndexError as exc:
            raise NotFoundError() from exc
        except (MediaError, OSError) as exc:
            log.warning("stream failed: %s", exc)
            raise UpstreamError("player") from exc

    async def info_of(item: Item, base: str):
        info = await probe(item)
        direct = item.path.suffix.lower() in DIRECT_EXTENSIONS
        return {
            "title": item.title, "subtitle": item.subtitle, "duration": info.duration,
            "container": info.container, "video_codec": info.video_codec,
            "codec_string": info.codec_string, "audio_codec": info.audio_codec,
            "direct_url": f"{base}/file" if direct else None,
            "hls_url": f"{base}/index.m3u8",
        }

    def file_of(item: Item):
        if item.path.suffix.lower() not in DIRECT_EXTENSIONS:
            raise NotFoundError()
        return FileResponse(item.path, media_type="video/mp4")

    async def playlist(item: Item, d: str | None):
        dev = device(d)
        if not dev:
            return _bad_device()
        info = await probe(item)
        # Starting the session here makes the cap apply to the playlist, not to a segment.
        res = await stream(sessions.init((dev, item.item_id), item.path, info))
        if isinstance(res, Response):
            return res
        segments = plan_segments(info.keyframes, info.duration)
        text = render_playlist(segments, init_uri=f"init.mp4?d={dev}",
                               seg_uri=lambda n: f"seg/{n}.m4s?d={dev}")
        return Response(text, media_type="application/vnd.apple.mpegurl",
                        headers={"Cache-Control": "no-cache"})

    async def init(item: Item, d: str | None):
        dev = device(d)
        if not dev:
            return _bad_device()
        info = await probe(item)
        res = await stream(sessions.init((dev, item.item_id), item.path, info))
        return res if isinstance(res, Response) else _send(res, "video/mp4")

    async def segment(item: Item, n: int, d: str | None):
        dev = device(d)
        if not dev:
            return _bad_device()
        info = await probe(item)
        res = await stream(sessions.segment((dev, item.item_id), item.path, info, n))
        return res if isinstance(res, Response) else _send(res, "video/iso.segment")

    # ---- films
    @router.get("/play/movie/{tmdb_id}")
    async def movie_info(tmdb_id: int):
        return await info_of(movie(tmdb_id), f"/api/play/movie/{tmdb_id}")

    @router.get("/play/movie/{tmdb_id}/file")
    def movie_file(tmdb_id: int):
        return file_of(movie(tmdb_id))

    @router.get("/play/movie/{tmdb_id}/index.m3u8")
    async def movie_playlist(tmdb_id: int, d: str | None = Query(None)):
        return await playlist(movie(tmdb_id), d)

    @router.get("/play/movie/{tmdb_id}/init.mp4")
    async def movie_init(tmdb_id: int, d: str | None = Query(None)):
        return await init(movie(tmdb_id), d)

    @router.get("/play/movie/{tmdb_id}/seg/{n}.m4s")
    async def movie_segment(tmdb_id: int, n: int, d: str | None = Query(None)):
        return await segment(movie(tmdb_id), n, d)

    # ---- episodes
    ep = "/play/episode/{show_id}/{season}/{episode}"

    @router.get(ep)
    async def episode_info(show_id: int, season: int, episode: int):
        return await info_of(resolve_episode(show_id, season, episode),
                             f"/api/play/episode/{show_id}/{season}/{episode}")

    @router.get(ep + "/file")
    def episode_file(show_id: int, season: int, episode: int):
        return file_of(resolve_episode(show_id, season, episode))

    @router.get(ep + "/index.m3u8")
    async def episode_playlist(show_id: int, season: int, episode: int,
                               d: str | None = Query(None)):
        return await playlist(resolve_episode(show_id, season, episode), d)

    @router.get(ep + "/init.mp4")
    async def episode_init(show_id: int, season: int, episode: int,
                           d: str | None = Query(None)):
        return await init(resolve_episode(show_id, season, episode), d)

    @router.get(ep + "/seg/{n}.m4s")
    async def episode_segment(show_id: int, season: int, episode: int, n: int,
                              d: str | None = Query(None)):
        return await segment(resolve_episode(show_id, season, episode), n, d)

    return router


def _bad_device() -> Response:
    return JSONResponse({"error": "bad_device"}, status_code=400)


def _send(path: Path, media_type: str) -> Response:
    # The file can vanish between StreamSessions returning and now (restart or trim).
    if not path.is_file():
        return Response(status_code=503, headers={"Retry-After": "1"})
    return FileResponse(path, media_type=media_type)
