from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from cinema.config import Settings, load_settings
from cinema.rqbit import RqbitClient
from cinema.store import Store
from cinema.webdav import build_dav_router


def create_app(settings: Settings, *, store: Store | None = None,
               rqbit: RqbitClient | None = None, start_poller: bool = True) -> FastAPI:
    owned_http: httpx.AsyncClient | None = None
    if rqbit is None:
        owned_http = httpx.AsyncClient()
        rqbit = RqbitClient(settings.rqbit_url, owned_http)
    if store is None:
        store = Store(settings.db_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if owned_http is not None:
            await owned_http.aclose()

    app = FastAPI(title="Cinema", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.rqbit = rqbit

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    auth = ((settings.dav_user, settings.dav_password)
            if settings.dav_user and settings.dav_password else None)
    app.include_router(build_dav_router(store, rqbit, rqbit.http, settings.legacy_dir, auth))

    return app


def build() -> FastAPI:
    return create_app(load_settings())
