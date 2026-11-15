import httpx
import pytest

from cinema.main import create_app

INDEX = '<!doctype html><title>Cinema</title><script type="module" src="assets/app.js"></script>'


@pytest.fixture
def static_dir(tmp_path):
    # A stand-in for the Vite build output, so the tests never need Node.
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(INDEX)
    (root / "assets" / "app.js").write_text("console.log('cinema')")
    return root


def make_client(settings, static_dir):
    app = create_app(settings, start_poller=False, static_dir=static_dir)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


@pytest.fixture
def client(settings, static_dir):
    return make_client(settings, static_dir)


@pytest.mark.anyio
async def test_root_serves_phone_app(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert resp.text == INDEX
    assert resp.headers["cache-control"] == "no-cache"
    asset = await client.get("/assets/app.js")
    assert asset.status_code == 200 and asset.text == "console.log('cinema')"


@pytest.mark.anyio
async def test_static_mount_does_not_shadow_other_routes(client):
    assert (await client.get("/healthz")).json() == {"ok": True}
    assert (await client.get("/api/movies/abc")).status_code == 422
    assert (await client.get("/api/search")).json() == {"films": [], "results": [], "people": [], "studios": [], "collections": []}
    # /dav answers with WebDAV, not the phone app's index.html.
    dav = await client.request("PROPFIND", "/dav/", headers={"Depth": "0"})
    assert dav.status_code == 207 and "text/html" not in dav.headers.get("content-type", "")


@pytest.mark.anyio
async def test_unknown_static_path_is_404(client):
    assert (await client.get("/nope.js")).status_code == 404


@pytest.mark.anyio
async def test_app_starts_without_a_built_phone_app(settings, tmp_path):
    client = make_client(settings, tmp_path / "missing")
    assert (await client.get("/healthz")).json() == {"ok": True}
    assert (await client.get("/api/search")).json() == {"films": [], "results": [], "people": [], "studios": [], "collections": []}
