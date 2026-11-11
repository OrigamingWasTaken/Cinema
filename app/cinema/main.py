from fastapi import FastAPI

from cinema.config import Settings, load_settings


def create_app(settings: Settings, **overrides) -> FastAPI:
    app = FastAPI(title="Cinema")
    app.state.settings = settings

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


def build() -> FastAPI:
    return create_app(load_settings())
