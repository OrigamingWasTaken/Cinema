import asyncio
import logging
import shutil
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from cinema.api import build_api_router, register_error_handlers
from cinema.config import Settings, load_settings
from cinema.downloads import run_poller
from cinema.hls import StreamSessions
from cinema.media import Prober
from cinema.play import build_play_router
from cinema.prowlarr import ProwlarrClient, api_key_from_config_xml
from cinema.rqbit import RqbitClient
from cinema.shows import ShowsTracker, run_shows_poller
from cinema.shows_service import ShowsService
from cinema.sonarr import SonarrClient
from cinema.store import Store
from cinema.tmdb import TmdbClient
from cinema.webdav import build_dav_router

# The built phone app (npm run build in app/web, or the Docker web stage).
STATIC_DIR = Path(__file__).parent / "static"
log = logging.getLogger(__name__)


class StaticApp(StaticFiles):
    """The phone app; revalidated on every load so a redeploy shows up at once."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


def _sonarr_key(settings: Settings) -> Callable[[], str]:
    """The key, read from Sonarr's config.xml again until it shows up (first start)."""
    known = settings.sonarr_api_key

    def key() -> str:
        nonlocal known
        if not known and settings.sonarr_config_xml is not None:
            known = api_key_from_config_xml(settings.sonarr_config_xml)
        return known
    return key


def _clear_dir(root: Path) -> None:
    """Empty `root` (never remove it), creating it if missing; failures are logged."""
    try:
        root.mkdir(parents=True, exist_ok=True)
        children = list(root.iterdir())
    except OSError as e:
        log.warning("cannot prepare %s: %s", root, e)
        return
    for child in children:
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        except OSError as e:
            log.warning("cannot remove %s: %s", child, e)


def create_app(settings: Settings, *, store: Store | None = None,
               rqbit: RqbitClient | None = None, tmdb: TmdbClient | None = None,
               prowlarr: ProwlarrClient | None = None, sonarr: SonarrClient | None = None,
               shows: ShowsService | None = None, prober: Prober | None = None,
               sessions: StreamSessions | None = None, start_poller: bool = True,
               clock: Callable[[], float] = time.monotonic,
               static_dir: Path | None = None) -> FastAPI:
    owned_http: httpx.AsyncClient | None = None
    if rqbit is None or tmdb is None or prowlarr is None or sonarr is None:
        owned_http = httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5))
    if rqbit is None:
        rqbit = RqbitClient(settings.rqbit_url, owned_http)
    if tmdb is None:
        tmdb = TmdbClient(settings.tmdb_token, settings.tmdb_language, owned_http)
    if prowlarr is None:
        prowlarr = ProwlarrClient(settings.prowlarr_url, settings.prowlarr_api_key, owned_http,
                                  config_xml=settings.prowlarr_config_xml)
    if sonarr is None:
        sonarr = SonarrClient(settings.sonarr_url, _sonarr_key(settings), owned_http)
    if store is None:
        store = Store(settings.db_path)
    tracker = ShowsTracker(store, sonarr, rqbit, downloads_dir=str(settings.downloads_dir))
    if shows is None:
        shows = ShowsService(store, sonarr, tmdb, tracker, shows_dir=str(settings.shows_dir))

    if prober is None:
        prober = Prober(settings.media_cache_dir)
    if sessions is None:
        sessions = StreamSessions(tmp_root=settings.hls_tmp_dir)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        # Leftovers of a crashed run.
        _clear_dir(sessions.tmp_root)
        reaper = asyncio.create_task(sessions.run_reaper())
        pollers = ([asyncio.create_task(run_poller(store, rqbit)),
                    asyncio.create_task(run_shows_poller(tracker))] if start_poller else [])
        try:
            yield
        finally:
            reaper.cancel()
            await asyncio.gather(reaper, return_exceptions=True)
            await sessions.close_all()
            for poller in pollers:
                poller.cancel()
            await asyncio.gather(*pollers, return_exceptions=True)
            if owned_http is not None:
                await owned_http.aclose()

    app = FastAPI(title="Cinema", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.rqbit = rqbit
    app.state.prowlarr = prowlarr
    app.state.sonarr = sonarr
    app.state.tracker = tracker

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    register_error_handlers(app)
    app.include_router(build_api_router(settings, store, rqbit, tmdb, prowlarr, clock, shows=shows,
                                     tracker=tracker),
                       prefix="/api")
    app.include_router(build_play_router(store, tracker, prober, sessions), prefix="/api")

    auth = ((settings.dav_user, settings.dav_password)
            if settings.dav_user and settings.dav_password else None)
    app.include_router(build_dav_router(store, rqbit, rqbit.http, settings.legacy_dir, auth,
                                         tracker))
    # Last: the catch-all mount must not shadow /api, /dav or /healthz.
    static_dir = STATIC_DIR if static_dir is None else static_dir
    if not static_dir.is_dir():
        # The API and WebDAV still work; only the phone app is missing until it is built.
        log.warning("phone app not built: %s does not exist", static_dir)
    app.mount("/", StaticApp(directory=static_dir, html=True, check_dir=False), name="static")

    return app


def build() -> FastAPI:
    return create_app(load_settings())
