import asyncio
import unicodedata
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from cinema.rqbit import RqbitClient
from cinema.shows import EpisodeEntry
from cinema.store import Store
from cinema.webdav import build_dav_router
from tests.test_webdav import CONTENT, NS, SIZE, Env, drive_disconnect, prop, propfind

KEY = (3, 2)  # the show torrent's (id, file index) in the fake rqbit
SHOW = "/dav/Shows/Breaking%20Bad%20%282008%29/"
S01 = SHOW + "Season%2001/"
E01 = S01 + quote("Breaking Bad - S01E01.mkv")
E01_RAW = "/dav/Shows/Breaking Bad (2008)/Season 01/Breaking Bad - S01E01.mkv"


def entry(**kw) -> EpisodeEntry:
    base = dict(series_id=5, show_tmdb_id=1396, title="Breaking Bad", year=2008, season=1,
                episode=1, last_episode=1, size=SIZE, progress_bytes=SIZE, mtime=1_700_000_000.0,
                path=None, torrent_id=KEY[0], file_index=KEY[1], download_bps=0.0, eta_s=None,
                state="ready")
    return EpisodeEntry(**{**base, **kw})


class ShowsEnv(Env):
    def __init__(self, tmp_path, settings):
        super().__init__(tmp_path, settings)
        self.tracker = self.app.state.tracker
        self.fake.content[KEY] = CONTENT
        self.fake.torrents[KEY[0]] = {"id": KEY[0], "paused": False, "deleted": False}

    def on_disk(self, name="ep.mkv", data=CONTENT) -> Path:
        path = self.tmp / "shows" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path


@pytest.fixture
def senv(tmp_path, settings):
    return ShowsEnv(tmp_path, settings)


def test_dav_root_lists_movies_and_shows(senv, tmp_path):
    c = senv.client()
    _, resp = propfind(c, "/dav/")
    assert set(resp) == {"/dav/", "/dav/Movies/", "/dav/Shows/"}
    assert resp["/dav/Shows/"].find("D:propstat/D:prop/D:resourcetype/D:collection", NS) is not None
    _, shows = propfind(c, "/dav/Shows")  # an empty Shows folder still answers
    assert set(shows) == {"/dav/Shows/"}

    # Without a tracker the share is Movies only, as before shows existed.
    app = FastAPI()
    app.include_router(build_dav_router(senv.store, senv.rqbit, senv.rqbit.http,
                                        tmp_path / "legacy", None))
    bare = TestClient(app)
    _, resp = propfind(bare, "/dav/")
    assert set(resp) == {"/dav/", "/dav/Movies/"}
    assert bare.request("PROPFIND", "/dav/Shows/").status_code == 404
    assert bare.get("/dav/Shows/x/Season 01/x.mkv").status_code == 404


def test_dav_shows_tree_depth1_each_level(senv):
    disk = senv.on_disk()
    senv.tracker.entries = [
        entry(),
        entry(episode=2, last_episode=3, path=disk, torrent_id=None, file_index=None,
              state="done", size=len(CONTENT), mtime=1_700_000_100.0),
        entry(season=2, episode=1, last_episode=1, ext=".mp4"),
        entry(episode=4, last_episode=4, state="preparing"),  # not listed yet
        entry(size=1),  # same name as S01E01: the first one wins
        entry(series_id=6, title="Dark", year=2017, ext=".avi"),
    ]
    c = senv.client()
    _, shows = propfind(c, "/dav/Shows/")
    assert set(shows) == {"/dav/Shows/", SHOW, "/dav/Shows/Dark%20%282017%29/"}
    assert prop(shows[SHOW], "displayname") == "Breaking Bad (2008)"
    assert shows[SHOW].find("D:propstat/D:prop/D:resourcetype/D:collection", NS) is not None

    _, seasons = propfind(c, SHOW)
    assert set(seasons) == {SHOW, S01, SHOW + "Season%2002/"}
    assert prop(seasons[S01], "displayname") == "Season 01"

    _, files = propfind(c, S01)
    e23 = S01 + quote("Breaking Bad - S01E02-E03.mkv")
    assert set(files) == {S01, E01, e23}
    assert prop(files[E01], "getcontentlength") == str(SIZE)
    assert prop(files[E01], "getcontenttype") == "video/x-matroska"
    assert prop(files[E01], "displayname") == "Breaking Bad - S01E01.mkv"
    assert prop(files[E01], "getlastmodified") == "Tue, 14 Nov 2023 22:13:20 GMT"
    assert files[E01].find("D:propstat/D:prop/D:resourcetype/D:collection", NS) is None
    assert prop(files[e23], "getcontentlength") == str(len(CONTENT))

    _, s2 = propfind(c, SHOW + "Season 02/")
    f = s2[SHOW + "Season%2002/" + quote("Breaking Bad - S02E01.mp4")]
    assert prop(f, "getcontenttype") == "video/mp4"
    _, dark = propfind(c, "/dav/Shows/Dark (2017)/Season 01/")
    assert prop(dark["/dav/Shows/Dark%20%282017%29/Season%2001/" + quote("Dark - S01E01.avi")],
                "getcontenttype") == "video/x-msvideo"

    _, one = propfind(c, E01)
    assert list(one) == [E01]
    for href in ("/dav/Shows/", SHOW, S01):
        _, self_only = propfind(c, href, depth="0")
        assert list(self_only) == [href]
    assert not [r for r in senv.fake.requests if "stream" in r["path"]]


def test_dav_shows_streams_in_progress_with_range(senv):
    senv.tracker.entries = [entry(progress_bytes=SIZE // 2)]
    c = senv.client()
    r = c.get(E01_RAW, headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.content == CONTENT[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{SIZE}"
    assert r.headers["content-length"] == "100"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "video/x-matroska"
    req = senv.fake.requests[-1]
    assert req["path"] == "/torrents/3/stream/2"
    assert req["headers"]["range"] == "bytes=100-199"

    r = c.get(E01_RAW, headers={"Range": "bytes=-500"})
    assert r.status_code == 206 and r.content == CONTENT[-500:]
    assert senv.fake.requests[-1]["headers"]["range"] == f"bytes={SIZE - 500}-{SIZE - 1}"
    assert c.get(E01_RAW).content == CONTENT

    n = len(senv.fake.requests)
    r = c.head(E01_RAW)
    assert r.status_code == 200
    assert r.headers["content-length"] == str(SIZE)
    assert r.headers["content-type"] == "video/x-matroska"
    assert len(senv.fake.requests) == n  # HEAD never reaches rqbit
    assert not +senv.tracker.open_streams


def test_dav_shows_serves_finished_from_disk(senv):
    path = senv.on_disk()
    senv.tracker.entries = [entry(path=path, torrent_id=None, file_index=None, state="done",
                                  size=len(CONTENT))]
    c = senv.client()
    r = c.get(E01_RAW, headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.content == CONTENT[10:20]
    assert r.headers["content-type"] == "video/x-matroska"
    assert c.head(E01_RAW).headers["content-length"] == str(len(CONTENT))
    assert not senv.fake.requests
    assert not +senv.tracker.open_streams
    path.unlink()
    assert c.get(E01_RAW).status_code == 404


def test_dav_shows_unicode_title(senv):
    title = "Amélie: Le Fabuleux / Destin."
    senv.tracker.entries = [entry(title=title, year=2001)]
    show, ep = "Amélie - Le Fabuleux - Destin (2001)", "Amélie - Le Fabuleux - Destin - S01E01.mkv"
    c = senv.client()
    _, shows = propfind(c, "/dav/Shows/")
    href = "/dav/Shows/" + quote(show) + "/"
    assert href in shows
    nfd = unicodedata.normalize("NFD", show)
    nfd_ep = unicodedata.normalize("NFD", ep)
    assert nfd != show
    _, seasons = propfind(c, f"/dav/Shows/{quote(nfd)}/")
    assert href + "Season%2001/" in seasons
    _, files = propfind(c, f"/dav/Shows/{quote(nfd)}/Season%2001/")
    assert href + "Season%2001/" + quote(ep) in files
    r = c.get(f"/dav/Shows/{quote(nfd)}/Season%2001/{quote(nfd_ep)}",
              headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and r.content == CONTENT[:10]


def asgi_get(app, path, receive):
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
             "raw_path": quote(path).encode(), "root_path": "", "query_string": b"",
             "headers": [], "client": ("test", 1), "server": ("test", 80)}
    return asyncio.wait_for(app(scope, receive, send), 5), sent


@pytest.mark.anyio
async def test_dav_shows_open_streams_counted_and_released_on_disconnect(senv):
    senv.tracker.entries = [entry()]
    senv.fake.chunk_size = 1024
    run, sent = drive_disconnect(senv.app, E01_RAW)
    seen: list[int] = []

    async def watch():
        while not any(m["type"] == "http.response.body" for m in sent):
            await asyncio.sleep(0)
        seen.append(senv.tracker.open_streams[KEY[0]])
    watcher = asyncio.ensure_future(watch())
    await run()
    await watcher
    assert sent[0]["status"] == 200
    assert seen == [1]  # counted while the stream was open
    assert senv.fake.closed_streams == [KEY]
    assert senv.tracker.open_streams[KEY[0]] == 0
    assert not +senv.tracker.open_streams  # released, never negative

    calls = 0

    async def receive():
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # the client stays

    # rqbit answers an error: opened, relayed whole and released.
    del senv.fake.content[KEY]
    done, sent = asgi_get(senv.app, E01_RAW, receive)
    await done
    assert sent[0]["status"] == 404
    assert not +senv.tracker.open_streams

    # The client leaves before rqbit answers: never counted.
    senv.fake.content[KEY] = CONTENT
    senv.fake.stream_gate = asyncio.Event()
    opens = sum("stream" in r["path"] for r in senv.fake.requests)
    calls = 0

    async def leaves():
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"type": "http.request", "body": b"", "more_body": False}
        while sum("stream" in r["path"] for r in senv.fake.requests) == opens:
            await asyncio.sleep(0.001)
        return {"type": "http.disconnect"}

    done, sent = asgi_get(senv.app, E01_RAW, leaves)
    await done
    assert senv.fake.cancelled_opens == [KEY]
    assert not +senv.tracker.open_streams


def test_dav_shows_502_when_rqbit_unreachable(senv, tmp_path):
    def boom(request):
        raise httpx.ConnectError("down", request=request)
    http = httpx.AsyncClient(transport=httpx.MockTransport(boom))
    rqbit = RqbitClient("http://rqbit:3030", http)
    senv.tracker.entries = [entry()]
    app = FastAPI()
    app.include_router(build_dav_router(Store(tmp_path / "x.db"), rqbit, http,
                                        tmp_path / "legacy", None, senv.tracker))
    assert TestClient(app).get(E01_RAW).status_code == 502
    assert not +senv.tracker.open_streams


def test_dav_shows_unknown_path_404(senv):
    senv.tracker.entries = [entry(), entry(episode=2, last_episode=2, state="preparing")]
    c = senv.client()
    for path in ("/dav/Shows/Nope/", "/dav/Shows/Breaking Bad (2008)/Season 09/",
                 "/dav/Shows/Breaking Bad (2008)/Season 01/nope.mkv",
                 "/dav/Shows/Breaking Bad (2008)/Season 01/Breaking Bad - S01E02.mkv",
                 "/dav/Shows/Breaking Bad (2008)/Season 01/Breaking Bad - S01E01.mkv/x"):
        assert c.request("PROPFIND", path).status_code == 404, path
        assert c.get(path).status_code == 404, path
    assert c.get("/dav/Shows/").status_code == 404
    assert c.get("/dav/Shows/Breaking Bad (2008)/").status_code == 404
