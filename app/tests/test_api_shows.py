import httpx
import pytest

from cinema.main import create_app
from cinema.prowlarr import ProwlarrClient
from cinema.rqbit import RqbitClient
from cinema.shows import EpisodeEntry
from cinema.sonarr import SonarrClient
from cinema.store import Store
from cinema.tmdb import TmdbClient
from tests.fakes import FakeRqbit, FakeSonarr
from tests.test_api import MOVIE, movie_row, prowlarr_handler

TVDB, TMDB = 81189, 1396
SHOW = {"id": TMDB, "name": "Breaking Bad", "first_air_date": "2008-01-20",
        "last_air_date": "2013-09-29", "overview": "o", "original_language": "en",
        "poster_path": "/bb.jpg", "backdrop_path": "/bbb.jpg", "in_production": False,
        "status": "Ended", "genres": [], "origin_country": ["US"],
        "external_ids": {"tvdb_id": TVDB}}
CARD = {"id": TMDB, "name": "Breaking Bad", "first_air_date": "2008-01-20",
        "poster_path": "/bb.jpg", "backdrop_path": "/bbb.jpg"}
OTHER = {"id": 5, "name": "Other", "first_air_date": "", "poster_path": None}


class TmdbStub:
    def __init__(self):
        self.shows = {TMDB: SHOW}
        self.lists_fail = False

    def __call__(self, request):
        path = request.url.path
        if path in ("/3/trending/tv/week", "/3/tv/popular"):
            if self.lists_fail:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json={"results": [CARD, OTHER]})
        if path.startswith("/3/tv/") and int(path.rsplit("/", 1)[1]) in self.shows:
            return httpx.Response(200, json=self.shows[int(path.rsplit("/", 1)[1])])
        if path == "/3/movie/693134":
            return httpx.Response(200, json=MOVIE)
        if path == "/3/search/tv":
            return httpx.Response(200, json={"results": [CARD]})
        if path.startswith("/3/search/"):
            return httpx.Response(200, json={"results": []})
        return httpx.Response(404, json={})


def series_dict(*, new_items="all", files=1, id=5):
    return {"id": id, "title": "Breaking Bad", "year": 2008, "tvdbId": TVDB, "tmdbId": TMDB,
            "status": "ended", "seriesType": "standard", "monitorNewItems": new_items,
            "seasons": [{"seasonNumber": 1, "monitored": True,
                         "statistics": {"episodeFileCount": files}}]}


def lookup_result():
    return {"title": "Breaking Bad", "year": 2008, "tvdbId": TVDB, "tmdbId": TMDB,
            "status": "ended", "seriesType": "standard", "monitorNewItems": "all",
            "seasons": [{"seasonNumber": 1, "monitored": True,
                         "statistics": {"episodeCount": 7}}]}


def entry(**kw) -> EpisodeEntry:
    base = dict(series_id=5, show_tmdb_id=TMDB, title="Breaking Bad", year=2008, season=1,
                episode=1, last_episode=1, size=1000, progress_bytes=250, mtime=1.0,
                path=None, torrent_id=0, file_index=0, download_bps=0.0, eta_s=60,
                state="preparing")
    return EpisodeEntry(**{**base, **kw})


def build(settings, tmp_path, *, sonarr_transport=None):
    fake = FakeSonarr()
    stub = TmdbStub()
    store = Store(tmp_path / "api.db")
    http = httpx.AsyncClient(transport=httpx.MockTransport(stub))
    app = create_app(
        settings, store=store,
        rqbit=RqbitClient("http://rqbit:3030", httpx.AsyncClient(
            transport=httpx.MockTransport(FakeRqbit([])._handle))),
        tmdb=TmdbClient("t", "en-US", http),
        prowlarr=ProwlarrClient("http://prowlarr:9696", "k", httpx.AsyncClient(
            transport=httpx.MockTransport(prowlarr_handler))),
        sonarr=SonarrClient("http://sonarr:8989", "k", httpx.AsyncClient(
            transport=sonarr_transport or fake.transport)),
        start_poller=False)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
    return client, app, fake, stub, store


def test_config_sonarr_defaults_and_lazy_key(tmp_path):
    from pathlib import Path

    from cinema.config import load_settings
    from cinema.sonarr import SonarrError

    s = load_settings({"TMDB_TOKEN": "t", "SONARR_CONFIG_XML": str(tmp_path / "s" / "c.xml")})
    assert s.sonarr_url == "http://sonarr:8989"
    assert s.shows_dir == Path("/data/shows") and s.sonarr_api_key == ""
    assert s.downloads_dir == Path("/data/downloads")
    assert load_settings({"TMDB_TOKEN": "t", "DOWNLOADS_DIR": "/dl"}).downloads_dir == \
        Path("/dl")
    assert load_settings({"TMDB_TOKEN": "t"}).sonarr_config_xml == \
        Path("/sonarr-config/config.xml")
    assert load_settings({"TMDB_TOKEN": "t", "SONARR_URL": "http://x:1", "SHOWS_DIR": "/s",
                          "SONARR_API_KEY": "env"}).sonarr_api_key == "env"
    app = create_app(s, store=Store(tmp_path / "x.db"), start_poller=False)
    assert app.state.tracker.downloads_dir == Path("/data/downloads")
    with pytest.raises(SonarrError):
        app.state.sonarr._api_key()
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "c.xml").write_text("<Config><ApiKey>fresh</ApiKey></Config>")
    assert app.state.sonarr._api_key() == "fresh"


@pytest.mark.anyio
async def test_shows_home_shape(settings, tmp_path):
    client, _, fake, _, _ = build(settings, tmp_path)
    fake.series[5] = series_dict()
    body = (await client.get("/api/shows")).json()
    assert body["yours"] == [{"id": TMDB, "title": "Breaking Bad", "year": 2008,
                              "poster_path": "/bb.jpg", "backdrop_path": "/bbb.jpg",
                              "in_library": True}]
    assert body["library"] == body["yours"]
    assert body["trending"][0]["in_library"] is True
    assert body["popular"][1]["in_library"] is False
    assert "sonarr_error" not in body and "catalog_error" not in body


@pytest.mark.anyio
async def test_shows_home_sonarr_down_flags_error(settings, tmp_path):
    client, _, fake, _, _ = build(settings, tmp_path)
    fake.fail_status = 500
    body = (await client.get("/api/shows")).json()
    assert body["yours"] == [] and body["library"] == []
    assert body["sonarr_error"] is True and "catalog_error" not in body
    assert len(body["trending"]) == 2


@pytest.mark.anyio
async def test_shows_home_tmdb_down_flags_catalog_error(settings, tmp_path):
    client, _, fake, stub, _ = build(settings, tmp_path)
    fake.series[5] = series_dict()
    stub.lists_fail = True
    body = (await client.get("/api/shows")).json()
    assert body["trending"] == [] and body["popular"] == []
    assert body["catalog_error"] is True and len(body["yours"]) == 1


@pytest.mark.anyio
async def test_show_page_404_unknown_tmdb(settings, tmp_path):
    client, *_ = build(settings, tmp_path)
    resp = await client.get("/api/shows/999")
    assert resp.status_code == 404 and resp.json() == {"error": "not_found"}


@pytest.mark.anyio
async def test_get_season_endpoint_returns_page(settings, tmp_path):
    client, _, fake, _, _ = build(settings, tmp_path)
    fake.series[5] = series_dict(files=0)
    fake.episodes = [{"id": 101, "seriesId": 5, "seasonNumber": 1, "episodeNumber": 1,
                      "title": "Pilot", "airDateUtc": "2008-01-20T02:00:00Z",
                      "monitored": False, "hasFile": False, "episodeFileId": 0}]
    resp = await client.post("/api/shows/1396/seasons/1")
    assert resp.status_code == 200
    page = resp.json()
    assert page["show"]["title"] == "Breaking Bad" and page["available"] is True
    assert page["seasons"][0]["number"] == 1 and page["seasons"][0]["state"] == "looking"
    assert any(c[2].get("name") == "SeasonSearch" for c in fake.calls
               if isinstance(c[2], dict))
    resp = await client.delete("/api/shows/1396/seasons/1")
    assert resp.status_code == 200 and resp.json()["seasons"][0]["state"] == "none"


@pytest.mark.anyio
@pytest.mark.parametrize("n", [0, 201])
async def test_season_number_is_validated(settings, tmp_path, n):
    client, *_ = build(settings, tmp_path)
    assert (await client.post(f"/api/shows/1396/seasons/{n}")).status_code == 422
    assert (await client.delete(f"/api/shows/1396/seasons/{n}")).status_code == 422


@pytest.mark.anyio
async def test_follow_not_airing_409(settings, tmp_path):
    client, *_ = build(settings, tmp_path)
    resp = await client.post("/api/shows/1396/follow")
    assert resp.status_code == 409 and resp.json() == {"error": "not_airing"}


@pytest.mark.anyio
async def test_follow_and_unfollow_return_page(settings, tmp_path):
    client, _, fake, stub, _ = build(settings, tmp_path)
    stub.shows[TMDB] = {**SHOW, "in_production": True}
    fake.series[5] = series_dict(new_items="none")
    page = (await client.post("/api/shows/1396/follow")).json()
    assert page["following"] is True
    page = (await client.delete("/api/shows/1396/follow")).json()
    assert page["following"] is False


@pytest.mark.anyio
async def test_show_unavailable_422(settings, tmp_path):
    client, _, _, stub, _ = build(settings, tmp_path)
    stub.shows[TMDB] = {**SHOW, "external_ids": {}}
    resp = await client.post("/api/shows/1396/seasons/1")
    assert resp.status_code == 422 and resp.json() == {"error": "not_available"}
    resp = await client.get("/api/shows/1396")  # the page itself still answers
    assert resp.status_code == 200 and resp.json()["available"] is False


@pytest.mark.anyio
async def test_shows_sonarr_error_502_service_sonarr(settings, tmp_path):
    client, _, fake, _, _ = build(settings, tmp_path)
    fake.fail_status = 500
    resp = await client.get("/api/shows/1396")
    assert resp.status_code == 502
    assert resp.json() == {"error": "upstream", "service": "sonarr"}


@pytest.mark.anyio
async def test_search_includes_shows(settings, tmp_path):
    client, *_ = build(settings, tmp_path)
    body = (await client.get("/api/search", params={"q": "breaking"})).json()
    assert body["shows"] == [{"id": TMDB, "title": "Breaking Bad", "year": 2008,
                              "poster_path": "/bb.jpg", "backdrop_path": "/bbb.jpg",
                              "in_library": False}]


@pytest.mark.anyio
async def test_home_episodes_from_tracker_without_sonarr_call(settings, tmp_path):
    client, app, fake, _, _ = build(settings, tmp_path)
    app.state.tracker.entries = [
        entry(episode=1, mtime=1.0, state="preparing", size=1000, progress_bytes=250,
              download_bps=0.0),
        entry(episode=2, mtime=3.0, state="ready", size=int(1e9), progress_bytes=int(5e8),
              download_bps=1e9),
        entry(episode=3, mtime=2.0, state="done", progress_bytes=1000),
        entry(episode=4, mtime=0.5, show_tmdb_id=777, state="preparing", size=0,
              progress_bytes=0),
    ]
    app.state.tracker.failed = [entry(episode=9, state="failed", eta_s=None)]
    body = (await client.get("/api/home")).json()
    assert [e["episode"] for e in body["episodes"]] == [2, 1, 4]
    first, second, third = body["episodes"]
    assert first == {"show_id": TMDB, "show_title": "Breaking Bad", "poster_path": "/bb.jpg",
                     "season": 1, "episode": 2, "state": "ready", "percent": 50, "eta_s": 60,
                     "may_buffer": False}
    assert second["percent"] == 25 and second["may_buffer"] is True
    assert third["poster_path"] is None and third["percent"] == 0  # TMDB 404 -> null
    assert [e["episode"] for e in body["episodes_failed"]] == [9]
    assert body["episodes_failed"][0]["state"] == "failed"
    assert fake.requests == []


@pytest.mark.anyio
async def test_home_works_when_sonarr_down(settings, tmp_path):
    def boom(request):
        raise httpx.ConnectError("sonarr is down")
    client, _, _, _, store = build(settings, tmp_path,
                                   sonarr_transport=httpx.MockTransport(boom))
    store.upsert(movie_row())
    home = await client.get("/api/home")
    assert home.status_code == 200 and home.json()["episodes"] == []
    assert (await client.get("/api/movies/693134")).json()["library"]["tmdb_id"] == 693134
    dav = await client.request("PROPFIND", "/dav/Movies", headers={"Depth": "1"})
    assert dav.status_code == 207
    assert (await client.get("/api/shows")).json()["sonarr_error"] is True
