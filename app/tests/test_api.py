import asyncio

import httpx
import pytest

from cinema.main import create_app
from cinema.prowlarr import ProwlarrClient
from cinema.rqbit import RqbitClient
from cinema.store import Movie, Store
from cinema.tmdb import TmdbClient
from tests.fakes import FakeRqbit
from tests.test_releases import load_fixture

MOVIE = {
    "id": 693134, "imdb_id": "tt15239678", "title": "Dune: Part Two",
    "original_title": "Dune: Part Two", "release_date": "2024-02-27", "runtime": 166,
    "original_language": "en", "overview": "Paul...", "poster_path": "/p.jpg",
    "backdrop_path": "/b.jpg",
}
CARD = {"id": 693134, "title": "Dune: Part Two", "release_date": "2024-02-27",
        "poster_path": "/p.jpg"}
OTHER = {"id": 5, "title": "Other", "release_date": "", "poster_path": None}
FILES = [(["Dune", "Dune.mkv"], 1000)]


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def prowlarr_items():
    return [{"guid": r["title"], "title": r["title"], "size": int(r["size_gb"] * 1e9),
             "seeders": r["seeders"], "indexer": "idx", "protocol": "torrent",
             "magnetUrl": f"magnet:?xt=urn:btih:{i:040x}"}
            for i, r in enumerate(load_fixture())]


def tmdb_handler(request):
    path = request.url.path
    if path == "/3/movie/693134":
        return httpx.Response(200, json=MOVIE)
    if path in ("/3/trending/movie/week", "/3/movie/popular", "/3/search/movie"):
        return httpx.Response(200, json={"results": [CARD, OTHER]})
    return httpx.Response(404, json={})


def prowlarr_handler(request):
    return httpx.Response(200, json=prowlarr_items())


def yielding(handler):
    """Make a mock upstream really await, so concurrent requests interleave."""
    async def slow(request):
        await asyncio.sleep(0.01)
        result = handler(request)
        return await result if asyncio.iscoroutine(result) else result
    return httpx.MockTransport(slow)


def build(settings, tmp_path, tmdb=tmdb_handler, prowlarr=prowlarr_handler, clock=None,
          rqbit_handler=None):
    fake = FakeRqbit(FILES)
    store = Store(tmp_path / "api.db")
    rqbit = RqbitClient("http://rqbit:3030", httpx.AsyncClient(
        transport=yielding(rqbit_handler or fake._handle)))
    t = TmdbClient("t", "en-US", httpx.AsyncClient(transport=yielding(tmdb)))
    p = ProwlarrClient("http://prowlarr:9696", "k",
                       httpx.AsyncClient(transport=yielding(prowlarr)))
    kwargs = {"clock": clock} if clock else {}
    app = create_app(settings, store=store, rqbit=rqbit, tmdb=t, prowlarr=p,
                     start_poller=False, **kwargs)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    return client, store, fake


async def recommended_id(client) -> str:
    body = (await client.get("/api/movies/693134/releases")).json()
    return body["recommended"]["id"]


def movie_row(**kw) -> Movie:
    base = dict(tmdb_id=693134, title="Dune: Part Two", year=2024, poster_path="/p.jpg",
                runtime_min=166, release_name="r", info_hash="a" * 40, torrent_id=0,
                file_index=0, file_path="/x/y.mkv", size=1000, status="downloading",
                added_at=1.0)
    return Movie(**{**base, **kw})


@pytest.mark.anyio
async def test_home_shape(settings, tmp_path):
    client, store, _ = build(settings, tmp_path)
    store.upsert(movie_row(tmdb_id=1, title="Old", added_at=1.0))
    store.upsert(movie_row(tmdb_id=693134, added_at=2.0))
    body = (await client.get("/api/home")).json()
    assert [m["tmdb_id"] for m in body["library"]] == [693134, 1]
    assert {"tmdb_id", "title", "year", "poster_path", "state", "percent"} <= set(body["library"][0])
    assert body["trending"][0] == {"id": 693134, "title": "Dune: Part Two", "year": 2024,
                                   "poster_path": "/p.jpg", "in_library": True}
    assert body["popular"][1]["in_library"] is False and body["popular"][1]["year"] is None
    assert set(body["disk"]) == {"free_bytes", "total_bytes"} and body["disk"]["total_bytes"] > 0
    assert "catalog_error" not in body


@pytest.mark.anyio
async def test_home_survives_tmdb_down(settings, tmp_path):
    client, store, _ = build(settings, tmp_path,
                             tmdb=lambda r: httpx.Response(500, text="boom"))
    store.upsert(movie_row())
    resp = await client.get("/api/home")
    body = resp.json()
    assert resp.status_code == 200 and body["catalog_error"] is True
    assert body["trending"] == [] and body["popular"] == []
    assert len(body["library"]) == 1


@pytest.mark.anyio
async def test_search_and_detail(settings, tmp_path):
    client, store, _ = build(settings, tmp_path)
    r = await client.get("/api/search", params={"q": "dune"})
    assert [c["id"] for c in r.json()["results"]] == [693134, 5]
    detail = (await client.get("/api/movies/693134")).json()
    assert detail["movie"]["runtime"] == 166 and detail["library"] is None
    store.upsert(movie_row())
    assert (await client.get("/api/movies/693134")).json()["library"]["tmdb_id"] == 693134


@pytest.mark.anyio
async def test_releases_recommended_and_groups(settings, tmp_path):
    client, _, _ = build(settings, tmp_path)
    body = (await client.get("/api/movies/693134/releases")).json()
    rec = body["recommended"]
    assert rec["resolution"] == 1440 and rec["source"] in {"bluray", "web"}
    assert body["groups"] and body["groups"][0]["resolution"] > body["groups"][-1]["resolution"]
    assert all(set(g) == {"resolution", "standard", "atmos"} for g in body["groups"])
    ids = {v["id"] for g in body["groups"] for v in g["standard"] + g["atmos"]}
    assert rec["id"] in ids
    assert any(v["sound"] == "atmos" for g in body["groups"] for v in g["atmos"])


@pytest.mark.anyio
async def test_release_view_never_exposes_names(settings, tmp_path):
    client, _, _ = build(settings, tmp_path)
    resp = await client.get("/api/movies/693134/releases")
    body = resp.json()
    views = [body["recommended"]] + [v for g in body["groups"] for v in g["standard"] + g["atmos"]]
    assert views
    for v in views:
        assert set(v) == {"id", "resolution", "size_bytes", "source", "sound", "lang", "speed"}
        assert not set(v) & {"title", "guid", "seeders", "indexer", "magnet", "url"}
    assert "SPARKS" not in resp.text and "magnet" not in resp.text


@pytest.mark.anyio
async def test_download_recommended_creates_library_item(settings, tmp_path):
    client, store, fake = build(settings, tmp_path)
    rid = await recommended_id(client)
    resp = await client.post("/api/movies/693134/download", json={"release_id": rid})
    assert resp.status_code == 201
    body = resp.json()
    assert body["tmdb_id"] == 693134 and body["title"] == "Dune: Part Two"
    assert body["year"] == 2024 and body["poster_path"] == "/p.jpg"
    m = store.get(693134)
    assert m.release_name.startswith("Dune.Part.Two.2024") and m.runtime_min == 166
    assert len(fake.torrents) == 1


@pytest.mark.anyio
async def test_download_twice_conflicts(settings, tmp_path):
    client, _, fake = build(settings, tmp_path)
    rid = await recommended_id(client)
    results = await asyncio.gather(*(
        client.post("/api/movies/693134/download", json={"release_id": rid})
        for _ in range(2)))
    assert sorted(r.status_code for r in results) == [201, 409]
    assert len(fake.torrents) == 1


@pytest.mark.anyio
async def test_download_after_failed_retries(settings, tmp_path):
    client, store, fake = build(settings, tmp_path)
    rid = await recommended_id(client)
    assert (await client.post("/api/movies/693134/download",
                              json={"release_id": rid})).status_code == 201
    store.upsert(movie_row(status="failed", torrent_id=0, file_path=str(tmp_path / "x.mkv")))
    resp = await client.post("/api/movies/693134/download", json={"release_id": rid})
    assert resp.status_code == 201
    assert fake.torrents[0]["deleted"] is True and len(fake.torrents) == 2
    assert store.get(693134).status == "downloading"


@pytest.mark.anyio
async def test_download_expired_release_410(settings, tmp_path):
    clock = Clock()
    client, store, fake = build(settings, tmp_path, clock=clock)
    rid = await recommended_id(client)
    assert (await client.post("/api/movies/693134/download",
                              json={"release_id": "nope"})).status_code == 410
    clock.t = 601
    resp = await client.post("/api/movies/693134/download", json={"release_id": rid})
    assert resp.status_code == 410
    assert store.get(693134) is None and fake.torrents == {}


@pytest.mark.anyio
async def test_releases_upstream_error_is_502(settings, tmp_path):
    client, _, _ = build(settings, tmp_path,
                         prowlarr=lambda r: httpx.Response(500, text="x"))
    resp = await client.get("/api/movies/693134/releases")
    assert resp.status_code == 502
    assert resp.json() == {"error": "upstream", "service": "prowlarr"}


@pytest.mark.anyio
async def test_malformed_upstream_is_502(settings, tmp_path):
    client, _, _ = build(settings, tmp_path,
                         tmdb=lambda r: httpx.Response(200, json={"id": 1}))
    resp = await client.get("/api/movies/693134")
    assert resp.status_code == 502 and resp.json()["service"] == "tmdb"


@pytest.mark.anyio
async def test_download_no_video_422(settings, tmp_path):
    client, store, fake = build(settings, tmp_path)
    fake.files = [(["x", "readme.txt"], 5)]
    rid = await recommended_id(client)
    resp = await client.post("/api/movies/693134/download", json={"release_id": rid})
    assert resp.status_code == 422 and resp.json() == {"error": "no_video"}
    assert store.get(693134) is None


@pytest.mark.anyio
async def test_delete_removes(settings, tmp_path):
    client, store, fake = build(settings, tmp_path)
    rid = await recommended_id(client)
    await client.post("/api/movies/693134/download", json={"release_id": rid})
    resp = await client.delete("/api/movies/693134")
    assert resp.status_code == 204
    assert store.get(693134) is None and fake.torrents[0]["deleted"] is True


@pytest.mark.anyio
async def test_search_tmdb_down_is_502(settings, tmp_path):
    client, _, _ = build(settings, tmp_path, tmdb=lambda r: httpx.Response(500, text="x"))
    resp = await client.get("/api/search", params={"q": "dune"})
    assert resp.status_code == 502
    assert resp.json() == {"error": "upstream", "service": "tmdb"}


@pytest.mark.anyio
async def test_download_rqbit_failure_is_502(settings, tmp_path):
    client, store, _ = build(settings, tmp_path,
                             rqbit_handler=lambda r: httpx.Response(500, text="boom"))
    rid = await recommended_id(client)
    resp = await client.post("/api/movies/693134/download", json={"release_id": rid})
    assert resp.status_code == 502
    assert resp.json() == {"error": "upstream", "service": "rqbit"}
    assert store.get(693134) is None


@pytest.mark.anyio
async def test_unknown_movie_is_404(settings, tmp_path):
    client, _, _ = build(settings, tmp_path,
                         tmdb=lambda r: httpx.Response(404, json={"success": False}))
    for path in ("/api/movies/1", "/api/movies/1/releases"):
        resp = await client.get(path)
        assert resp.status_code == 404 and resp.json() == {"error": "not_found"}
    home = await client.get("/api/home")
    assert home.status_code == 200 and home.json()["catalog_error"] is True

