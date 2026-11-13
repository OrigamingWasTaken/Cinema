import asyncio
import base64
import xml.etree.ElementTree as ET
from dataclasses import replace
from email.utils import formatdate
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from cinema.main import create_app
from cinema.rqbit import RqbitClient
from cinema.store import Movie, Store
from cinema.webdav import READY_BYTES
from tests.fakes import FakeRqbit

NS = {"D": "DAV:"}
SIZE = 3 * 1024**2
CONTENT = bytes(i % 251 for i in range(SIZE))
STREAM_KEY = (0, 0)


def make_movie(tmdb_id=1, title="Sintel", year=2010, **kw) -> Movie:
    base = dict(tmdb_id=tmdb_id, title=title, year=year, poster_path=None, runtime_min=None,
                release_name="x", info_hash="a" * 40, torrent_id=0, file_index=0,
                file_path="/nonexistent/Sintel.mkv", size=SIZE, status="done",
                progress_bytes=SIZE, added_at=1_700_000_000.0)
    base.update(kw)
    return Movie(**base)


class Env:
    def __init__(self, tmp_path, settings, **settings_kw):
        self.tmp = tmp_path
        self.settings = replace(settings, **settings_kw)
        self.store = Store(tmp_path / "db" / "cinema.db")
        self.fake = FakeRqbit()
        self.fake.content[STREAM_KEY] = CONTENT
        self.fake.torrents[0] = {"id": 0, "paused": False, "deleted": False}
        self.rqbit = RqbitClient("http://rqbit:3030",
                                 httpx.AsyncClient(transport=self.fake.transport))
        self.app = create_app(self.settings, store=self.store, rqbit=self.rqbit,
                              start_poller=False)

    def client(self, **kw) -> TestClient:
        return TestClient(self.app, **kw)

    def add_done(self, **kw) -> Movie:
        m = make_movie(**kw)
        folder = self.tmp / "movies" / "Sintel (2010)"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "Sintel.mkv"
        path.write_bytes(CONTENT)
        m = replace(m, file_path=str(path))
        self.store.upsert(m)
        return m

    def add_downloading(self, **kw) -> Movie:
        m = make_movie(status="downloading", progress_bytes=READY_BYTES, size=SIZE, **kw)
        self.store.upsert(m)
        self.fake.available[STREAM_KEY] = SIZE
        return m


@pytest.fixture
def env(tmp_path, settings):
    return Env(tmp_path, settings)


def propfind(client, path, depth="1"):
    r = client.request("PROPFIND", path, headers={"Depth": depth})
    assert r.status_code == 207
    root = ET.fromstring(r.content)
    out = {}
    for resp in root.findall("D:response", NS):
        out[resp.findtext("D:href", namespaces=NS)] = resp
    return r, out


def prop(resp, name):
    return resp.findtext(f"D:propstat/D:prop/D:{name}", namespaces=NS)


def test_propfind_root_lists_movies_dir(env):
    c = env.client()
    r, resp = propfind(c, "/dav/")
    assert r.headers["content-type"] == "application/xml; charset=utf-8"
    assert set(resp) == {"/dav/", "/dav/Movies/"}
    assert resp["/dav/Movies/"].find("D:propstat/D:prop/D:resourcetype/D:collection", NS) is not None
    _, depth0 = propfind(c, "/dav/", depth="0")
    assert set(depth0) == {"/dav/"}
    _, no_slash = propfind(c, "/dav")
    assert set(no_slash) == {"/dav/", "/dav/Movies/"}


def test_propfind_movies_hides_not_ready(env):
    env.add_downloading()
    env.store.upsert(replace(env.store.get(1), progress_bytes=READY_BYTES - 1))
    c = env.client()
    _, resp = propfind(c, "/dav/Movies/")
    assert set(resp) == {"/dav/Movies/"}
    env.store.upsert(replace(env.store.get(1), progress_bytes=READY_BYTES))
    _, resp = propfind(c, "/dav/Movies")
    assert set(resp) == {"/dav/Movies/", "/dav/Movies/Sintel%20%282010%29/"}


def test_not_ready_and_failed_are_404(env):
    env.add_downloading()
    env.store.upsert(replace(env.store.get(1), progress_bytes=READY_BYTES - 1))
    c = env.client()
    assert c.get("/dav/Movies/Sintel (2010)/Sintel (2010).mkv").status_code == 404
    env.store.upsert(replace(env.store.get(1), status="failed", progress_bytes=SIZE))
    assert c.get("/dav/Movies/Sintel (2010)/Sintel (2010).mkv").status_code == 404
    assert c.request("PROPFIND", "/dav/Movies/Sintel (2010)/").status_code == 404


def test_propfind_reports_final_size_while_downloading(env):
    env.add_downloading()
    env.fake.available[STREAM_KEY] = 1024**2
    _, resp = propfind(env.client(), "/dav/Movies/Sintel (2010)/")
    f = resp["/dav/Movies/Sintel%20%282010%29/Sintel%20%282010%29.mkv"]
    assert prop(f, "getcontentlength") == str(SIZE)
    assert prop(f, "displayname") == "Sintel (2010).mkv"
    assert prop(f, "getcontenttype") == "video/x-matroska"
    assert prop(f, "getlastmodified") == formatdate(1_700_000_000.0, usegmt=True)
    assert f.find("D:propstat/D:prop/D:resourcetype/D:collection", NS) is None
    assert not [r for r in env.fake.requests if "stream" in r["path"]]


def test_propfind_file_depth_1_is_self_only(env):
    env.add_done()
    _, resp = propfind(env.client(), "/dav/Movies/Sintel (2010)/Sintel (2010).mkv")
    assert list(resp) == ["/dav/Movies/Sintel%20%282010%29/Sintel%20%282010%29.mkv"]


def test_get_done_file_range_from_disk(env):
    env.add_done()
    r = env.client().get("/dav/Movies/Sintel (2010)/Sintel (2010).mkv",
                         headers={"Range": "bytes=10-19"})
    assert r.status_code == 206
    assert r.content == CONTENT[10:20]
    assert r.headers["content-type"] == "video/x-matroska"
    assert not env.fake.requests


def test_get_downloading_proxies_range_to_rqbit(env):
    env.add_downloading()
    r = env.client().get("/dav/Movies/Sintel (2010)/Sintel (2010).mkv",
                         headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.content == CONTENT[100:200]
    assert r.headers["content-range"] == f"bytes 100-199/{SIZE}"
    assert r.headers["content-length"] == "100"
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "video/x-matroska"
    req = env.fake.requests[-1]
    assert req["path"] == "/torrents/0/stream/0"
    assert req["headers"]["range"] == "bytes=100-199"


def test_get_downloading_without_range_is_200(env):
    env.add_downloading()
    r = env.client().get("/dav/Movies/Sintel (2010)/Sintel (2010).mkv")
    assert r.status_code == 200
    assert r.content == CONTENT


def test_head_downloading_answered_locally(env):
    env.add_downloading()
    r = env.client().head("/dav/Movies/Sintel (2010)/Sintel (2010).mkv")
    assert r.status_code == 200
    assert r.headers["content-length"] == str(SIZE)
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "video/x-matroska"
    assert not env.fake.requests


def test_head_done_file(env):
    env.add_done()
    r = env.client().head("/dav/Movies/Sintel (2010)/Sintel (2010).mkv")
    assert r.status_code == 200
    assert r.headers["content-length"] == str(SIZE)


@pytest.mark.parametrize("kind", ["disk", "proxy"])
def test_dav_range_variants(env, kind):
    (env.add_done if kind == "disk" else env.add_downloading)()
    c = env.client()
    url = "/dav/Movies/Sintel (2010)/Sintel (2010).mkv"
    r = c.get(url, headers={"Range": f"bytes={SIZE - 100}-"})
    assert r.status_code == 206 and r.content == CONTENT[-100:]
    r = c.get(url, headers={"Range": "bytes=-500"})
    assert r.status_code == 206 and r.content == CONTENT[-500:]
    r = c.get(url, headers={"Range": f"bytes={SIZE}-"})
    assert r.status_code == 416


def test_proxy_suffix_range_is_rewritten_to_absolute(env):
    env.add_downloading()
    r = env.client().get("/dav/Movies/Sintel (2010)/Sintel (2010).mkv",
                         headers={"Range": "bytes=-500"})
    assert r.status_code == 206
    assert r.content == CONTENT[-500:]
    assert r.headers["content-range"] == f"bytes {SIZE - 500}-{SIZE - 1}/{SIZE}"
    assert env.fake.requests[-1]["headers"]["range"] == f"bytes={SIZE - 500}-{SIZE - 1}"


def test_proxy_suffix_larger_than_file_returns_whole_file(env):
    env.add_downloading()
    r = env.client().get("/dav/Movies/Sintel (2010)/Sintel (2010).mkv",
                         headers={"Range": f"bytes=-{SIZE * 2}"})
    assert r.status_code == 206
    assert r.content == CONTENT
    assert r.headers["content-range"] == f"bytes 0-{SIZE - 1}/{SIZE}"
    assert env.fake.requests[-1]["headers"]["range"] == f"bytes=0-{SIZE - 1}"


def test_proxy_zero_suffix_is_416_answered_locally(env):
    env.add_downloading()
    r = env.client().get("/dav/Movies/Sintel (2010)/Sintel (2010).mkv",
                         headers={"Range": "bytes=-0"})
    assert r.status_code == 416
    assert r.headers["content-range"] == f"bytes */{SIZE}"
    assert not env.fake.requests


def test_proxy_passes_other_range_forms_through_unchanged(env):
    env.add_downloading()
    c = env.client()
    url = "/dav/Movies/Sintel (2010)/Sintel (2010).mkv"
    for rng in ("bytes=5-", "bytes=0-1,5-9", "bytes=abc"):
        c.get(url, headers={"Range": rng})
        assert env.fake.requests[-1]["headers"]["range"] == rng


def test_fake_rqbit_ignores_suffix_ranges_like_real_rqbit(env):
    async def go():
        async with httpx.AsyncClient(transport=env.fake.transport) as h:
            return await h.get("http://rqbit/torrents/0/stream/0", headers={"Range": "bytes=-500"})
    r = asyncio.run(go())
    assert r.status_code == 200
    assert "content-range" not in r.headers
    assert r.content == CONTENT


def drive_disconnect(app, path):
    """Run the ASGI app, delivering http.disconnect right after the first body chunk."""
    sent: list[dict] = []
    first_chunk = asyncio.Event()

    async def receive():
        if not sent and not first_chunk.is_set() and not getattr(receive, "started", False):
            receive.started = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await first_chunk.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            first_chunk.set()

    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
             "http_version": "1.1", "method": "GET", "scheme": "http", "path": path,
             "raw_path": quote(path).encode(), "root_path": "", "query_string": b"",
             "headers": [], "client": ("test", 1), "server": ("test", 80)}

    async def run():
        await asyncio.wait_for(app(scope, receive, send), 5)

    return run, sent


@pytest.mark.anyio
async def test_proxy_closes_upstream_on_disconnect(env):
    env.add_downloading()
    env.fake.chunk_size = 1024  # 3072 chunks
    run, sent = drive_disconnect(env.app, "/dav/Movies/Sintel (2010)/Sintel (2010).mkv")
    await run()
    assert sent[0]["status"] == 200
    assert env.fake.closed_streams == [STREAM_KEY]
    bodies = [m for m in sent if m["type"] == "http.response.body" and m.get("body")]
    assert 1 <= len(bodies) < SIZE // 1024
    assert sum(len(m["body"]) for m in bodies) < SIZE


def test_dav_unicode_href_roundtrip(env):
    env.add_done(title="Amélie", year=2001)
    # the helper writes to a fixed folder; the name comes from the DB
    c = env.client()
    _, resp = propfind(c, "/dav/Movies/")
    href = "/dav/Movies/Am%C3%A9lie%20%282001%29/"
    assert href in resp
    _, files = propfind(c, href)
    file_href = href + quote("Amélie (2001).mkv")
    assert file_href in files
    r = c.get("/dav/Movies/Amélie (2001)/Amélie (2001).mkv")
    assert r.status_code == 200 and r.content == CONTENT
    assert c.get(file_href).content == CONTENT


def test_legacy_dir_listed(env):
    legacy = env.settings.legacy_dir / "Old Film (1999)"
    legacy.mkdir(parents=True)
    (legacy / "old.film.mp4").write_bytes(b"legacy-bytes")
    (legacy / "readme.txt").write_text("x")
    (env.settings.legacy_dir / "Empty").mkdir()
    env.add_done()
    # a legacy dir colliding with a DB name loses
    clash = env.settings.legacy_dir / "Sintel (2010)"
    clash.mkdir()
    (clash / "other.mkv").write_bytes(b"zzz")
    c = env.client()
    _, resp = propfind(c, "/dav/Movies/")
    assert set(resp) == {"/dav/Movies/", "/dav/Movies/Old%20Film%20%281999%29/",
                         "/dav/Movies/Sintel%20%282010%29/"}
    _, files = propfind(c, "/dav/Movies/Old Film (1999)/")
    f = files["/dav/Movies/Old%20Film%20%281999%29/old.film.mp4"]
    assert prop(f, "getcontentlength") == str(len(b"legacy-bytes"))
    assert prop(f, "getcontenttype") == "video/mp4"
    assert prop(f, "getlastmodified").endswith("GMT")
    r = c.get("/dav/Movies/Old Film (1999)/old.film.mp4", headers={"Range": "bytes=0-5"})
    assert r.status_code == 206 and r.content == b"legacy"
    _, sintel = propfind(c, "/dav/Movies/Sintel (2010)/")
    assert "/dav/Movies/Sintel%20%282010%29/Sintel%20%282010%29.mkv" in sintel
    assert c.get("/dav/Movies/Sintel (2010)/other.mkv").status_code == 404


def test_unknown_path_404(env):
    c = env.client()
    assert c.request("PROPFIND", "/dav/Nope/").status_code == 404
    assert c.get("/dav/Movies/Nope/x.mkv").status_code == 404


def test_options(env):
    r = env.client().options("/dav/anything/at/all")
    assert r.status_code == 200
    assert r.headers["dav"] == "1"
    assert r.headers["allow"] == "OPTIONS, PROPFIND, GET, HEAD"


def test_basic_auth_required_when_configured(tmp_path, settings):
    env = Env(tmp_path, settings, dav_user="u", dav_password="p")
    env.add_done()
    c = env.client()
    r = c.request("PROPFIND", "/dav/")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == 'Basic realm="Cinema"'
    assert c.options("/dav/").status_code == 401
    bad = base64.b64encode(b"u:wrong").decode()
    assert c.get("/dav/", headers={"Authorization": f"Basic {bad}"}).status_code == 401
    good = base64.b64encode(b"u:p").decode()
    r = c.request("PROPFIND", "/dav/", headers={"Authorization": f"Basic {good}"})
    assert r.status_code == 207


def test_unsupported_method_405(env):
    c = env.client()
    assert c.put("/dav/Movies/x.mkv", content=b"x").status_code == 405
    assert c.delete("/dav/Movies/").status_code == 405


def test_create_app_rejects_unknown_override(settings):
    with pytest.raises(TypeError):
        create_app(settings, bogus=1)
