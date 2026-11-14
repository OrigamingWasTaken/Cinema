import asyncio
import time
from collections.abc import Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from cinema.api import build_api_router, register_error_handlers
from cinema.config import Settings, load_settings
from cinema.downloads import run_poller
from cinema.prowlarr import ProwlarrClient
from cinema.rqbit import RqbitClient
from cinema.store import Store
from cinema.tmdb import TmdbClient
from cinema.webdav import build_dav_router


def create_app(settings: Settings, *, store: Store | None = None,
               rqbit: RqbitClient | None = None, tmdb: TmdbClient | None = None,
               prowlarr: ProwlarrClient | None = None, start_poller: bool = True,
               clock: Callable[[], float] = time.monotonic) -> FastAPI:
    owned_http: httpx.AsyncClient | None = None
    if rqbit is None or tmdb is None or prowlarr is None:
        owned_http = httpx.AsyncClient()
    if rqbit is None:
        rqbit = RqbitClient(settings.rqbit_url, owned_http)
    if tmdb is None:
        tmdb = TmdbClient(settings.tmdb_token, settings.tmdb_language, owned_http)
    if prowlarr is None:
        prowlarr = ProwlarrClient(settings.prowlarr_url, settings.prowlarr_api_key, owned_http)
    if store is None:
        store = Store(settings.db_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        poller = asyncio.create_task(run_poller(store, rqbit)) if start_poller else None
        try:
            yield
        finally:
            if poller is not None:
                poller.cancel()
                await asyncio.gather(poller, return_exceptions=True)
            if owned_http is not None:
                await owned_http.aclose()

    app = FastAPI(title="Cinema", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.rqbit = rqbit

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    register_error_handlers(app)
    app.include_router(build_api_router(settings, store, rqbit, tmdb, prowlarr, clock),
                       prefix="/api")

    auth = ((settings.dav_user, settings.dav_password)
            if settings.dav_user and settings.dav_password else None)
    app.include_router(build_dav_router(store, rqbit, rqbit.http, settings.legacy_dir, auth))

    return app


def build() -> FastAPI:
    return create_app(load_settings())
