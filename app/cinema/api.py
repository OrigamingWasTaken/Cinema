import asyncio
import logging
import hashlib
import shutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import TypeVar

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from cinema.config import Settings
from cinema.downloads import (AlreadyInLibraryError, NoVideoFileError, remove_movie,
                              start_download, status_view)
from cinema.prowlarr import ProwlarrClient, ProwlarrError
from cinema.ranking import more_groups, recommended, speed_label
from cinema.releases import Classified, Release, classify
from cinema.rqbit import RqbitClient, RqbitError
from cinema.store import Movie, Store
from cinema.tmdb import MovieCard, TmdbClient, TmdbError

log = logging.getLogger(__name__)
RELEASE_TTL = 600.0
# A malformed upstream payload surfaces as one of these from a client; treat it as that
# service being down rather than a 500.
_MALFORMED = (KeyError, TypeError, AttributeError, ValueError)
T = TypeVar("T")


class UpstreamError(Exception):
    def __init__(self, service: str):
        super().__init__(service)
        self.service = service


class NotFoundError(Exception):
    pass


class DownloadRequest(BaseModel):
    release_id: str


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def _not_found(_, exc: NotFoundError):
        return JSONResponse({"error": "not_found"}, status_code=404)

    @app.exception_handler(UpstreamError)
    async def _upstream(_, exc: UpstreamError):
        return JSONResponse({"error": "upstream", "service": exc.service}, status_code=502)


async def _call(service: str, errors: tuple[type[Exception], ...],
                awaitable: Awaitable[T]) -> T:
    try:
        return await awaitable
    except (*errors, *_MALFORMED) as exc:
        if isinstance(exc, TmdbError) and exc.status == 404:
            raise NotFoundError() from exc
        log.warning("%s failed: %s", service, exc)
        raise UpstreamError(service) from exc


async def _rqbit(awaitable: Awaitable[T]) -> T:
    # Only rqbit's own failures are 502; a bug in our code must surface as a 500.
    try:
        return await awaitable
    except RqbitError as exc:
        log.warning("rqbit failed: %s", exc)
        raise UpstreamError("rqbit") from exc


def release_id(release: Release) -> str:
    return hashlib.sha1(release.guid.encode()).hexdigest()[:12]


def _card(c: MovieCard, store: Store) -> dict:
    return {"id": c.id, "title": c.title, "year": c.year, "poster_path": c.poster_path,
            "in_library": store.get(c.id) is not None}


def _library_item(m: Movie) -> dict:
    return {"tmdb_id": m.tmdb_id, "title": m.title, "year": m.year,
            "poster_path": m.poster_path, **status_view(m)}


_SOURCE = {"remux": "bluray", "bluray": "bluray", "webdl": "web", "webrip": "web"}
_LANG = {"multi": "multi", "french_subs": "french_subs"}


def _release_view(c: Classified) -> dict:
    return {
        "id": release_id(c.release),
        "resolution": c.resolution,
        "size_bytes": c.release.size,
        "source": _SOURCE.get(c.source, "other"),
        "sound": "atmos" if c.atmos else c.channels,
        # french_only only survives filtering for fr films, where it is the original audio.
        "lang": _LANG.get(c.lang, "original"),
        "speed": speed_label(c.release.seeders),
    }


def _disk(path: Path) -> dict:
    probe = Path(path)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    return {"free_bytes": usage.free, "total_bytes": usage.total}


def build_api_router(settings: Settings, store: Store, rqbit: RqbitClient,
                     tmdb: TmdbClient, prowlarr: ProwlarrClient,
                     clock: Callable[[], float] = time.monotonic) -> APIRouter:
    router = APIRouter()
    release_cache: dict[int, tuple[float, dict[str, Release]]] = {}
    locks: dict[int, asyncio.Lock] = {}

    def lock_for(tmdb_id: int) -> asyncio.Lock:
        return locks.setdefault(tmdb_id, asyncio.Lock())

    @router.get("/home")
    async def home():
        trending: list[dict] = []
        popular: list[dict] = []
        catalog_error = False
        try:
            trending = [_card(c, store) for c in await _call("tmdb", (TmdbError,), tmdb.trending())]
            popular = [_card(c, store) for c in await _call("tmdb", (TmdbError,), tmdb.popular())]
        except (UpstreamError, NotFoundError) as exc:
            log.warning("catalog unavailable: %s", type(exc).__name__)
            trending, popular, catalog_error = [], [], True
        body = {
            "library": [_library_item(m) for m in store.all()],
            "trending": trending,
            "popular": popular,
            "disk": _disk(settings.movies_dir),
        }
        if catalog_error:
            body["catalog_error"] = True
        return body

    @router.get("/search")
    async def search(q: str = ""):
        query = q.strip()
        if not query:
            return {"results": []}
        cards = await _call("tmdb", (TmdbError,), tmdb.search(query))
        return {"results": [_card(c, store) for c in cards]}

    @router.get("/movies/{tmdb_id}")
    async def movie_detail(tmdb_id: int):
        info = await _call("tmdb", (TmdbError,), tmdb.movie(tmdb_id))
        m = store.get(tmdb_id)
        return {"movie": asdict(info), "library": _library_item(m) if m else None}

    @router.get("/movies/{tmdb_id}/releases")
    async def releases(tmdb_id: int):
        info = await _call("tmdb", (TmdbError,), tmdb.movie(tmdb_id))
        queries = [f"{info.title} {info.year}" if info.year else info.title]
        if info.original_title != info.title:
            queries.append(f"{info.original_title} {info.year}" if info.year
                           else info.original_title)
        found = await _call("prowlarr", (ProwlarrError,), prowlarr.search(queries))
        classified = await asyncio.to_thread(lambda: [classify(r) for r in found])
        best = recommended(classified, info)
        groups = more_groups(classified, info)
        release_cache[tmdb_id] = (clock(), {release_id(r): r for r in found})
        return {
            "recommended": _release_view(best) if best else None,
            "groups": [{"resolution": g.resolution,
                        "standard": [_release_view(c) for c in g.standard],
                        "atmos": [_release_view(c) for c in g.atmos]} for g in groups],
        }

    def cached_release(tmdb_id: int, rid: str) -> Release | None:
        entry = release_cache.get(tmdb_id)
        if entry is None:
            return None
        stamp, by_id = entry
        if clock() - stamp >= RELEASE_TTL:
            del release_cache[tmdb_id]
            return None
        return by_id.get(rid)

    @router.post("/movies/{tmdb_id}/download", status_code=201)
    async def download(tmdb_id: int, body: DownloadRequest):
        async with lock_for(tmdb_id):
            existing = store.get(tmdb_id)
            if existing is not None and existing.status != "failed":
                return JSONResponse({"error": "already_in_library"}, status_code=409)
            release = cached_release(tmdb_id, body.release_id)
            if release is None:
                return JSONResponse({"error": "release_expired"}, status_code=410)
            info = await _call("tmdb", (TmdbError,), tmdb.movie(tmdb_id))
            source = await _call("prowlarr", (ProwlarrError,), prowlarr.resolve(release))
            try:
                if existing is not None:
                    await _rqbit(remove_movie(store, rqbit, settings.movies_dir, tmdb_id))
                movie = await _rqbit(start_download(
                    store, rqbit, settings.movies_dir, tmdb_id=tmdb_id, title=info.title,
                    year=info.year, poster_path=info.poster_path, runtime_min=info.runtime,
                    release_name=release.title, source=source))
            except AlreadyInLibraryError:
                return JSONResponse({"error": "already_in_library"}, status_code=409)
            except NoVideoFileError:
                return JSONResponse({"error": "no_video"}, status_code=422)
            return _library_item(movie)

    @router.delete("/movies/{tmdb_id}", status_code=204)
    async def delete_movie(tmdb_id: int):
        async with lock_for(tmdb_id):
            await _rqbit(remove_movie(store, rqbit, settings.movies_dir, tmdb_id))
        return Response(status_code=204)

    return router
