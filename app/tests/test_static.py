import httpx
import pytest

from cinema.main import create_app


@pytest.fixture
def client(settings):
    app = create_app(settings, start_poller=False)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


@pytest.mark.anyio
async def test_root_serves_phone_app(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert '<script type="module" src="app.js">' in resp.text
    assert resp.headers["cache-control"] == "no-cache"
    assert (await client.get("/app.js")).status_code == 200


@pytest.mark.anyio
async def test_static_mount_does_not_shadow_other_routes(client):
    assert (await client.get("/healthz")).json() == {"ok": True}
    assert (await client.get("/api/movies/abc")).status_code == 422
