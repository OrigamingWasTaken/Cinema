import httpx
import pytest

from cinema.prowlarr import ProwlarrClient, ProwlarrError
from cinema.releases import Release

BASE = "http://prowlarr:9696"
HASH = "ABCDEF0123456789ABCDEF0123456789ABCDEF01"


def make(handler, key="k"):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ProwlarrClient(BASE, key, http)


def item(guid, **kw):
    base = {"guid": guid, "title": f"T {guid}", "size": 1000, "seeders": 5,
            "indexer": "idx", "protocol": "torrent", "downloadUrl": None,
            "magnetUrl": None, "infoHash": None}
    return {**base, **kw}


def release(**kw):
    base = {"guid": "g", "title": "Dune 2021 1080p", "size": 1, "seeders": 1,
            "indexer": "i", "download_url": None, "magnet": None, "info_hash": None}
    return Release(**{**base, **kw})


@pytest.mark.anyio
async def test_search_params_and_header():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[item("a", seeders=None, size=None)])

    out = await make(handler).search(["dune", "dune 2021"])
    assert sorted(r.url.params["query"] for r in seen) == ["dune", "dune 2021"]
    p = seen[0].url.params
    assert seen[0].url.path == "/api/v1/search"
    assert p["type"] == "movie" and p["categories"] == "2000" and p["limit"] == "100"
    assert seen[0].headers["x-api-key"] == "k"
    assert out[0].seeders == 0 and out[0].size == 0


@pytest.mark.anyio
async def test_search_dedupes_by_infohash():
    def handler(request):
        if request.url.params["query"] == "a":
            return httpx.Response(200, json=[item("g1", infoHash=HASH), item("g2")])
        return httpx.Response(200, json=[item("g3", infoHash=HASH.lower()), item("g2")])

    out = await make(handler).search(["a", "b"])
    assert sorted(r.guid for r in out) == ["g1", "g2"]


@pytest.mark.anyio
async def test_search_drops_usenet():
    def handler(request):
        return httpx.Response(200, json=[item("n", protocol="usenet"), item("t")])

    out = await make(handler).search(["x"])
    assert [r.guid for r in out] == ["t"]


@pytest.mark.anyio
async def test_search_upstream_error():
    with pytest.raises(ProwlarrError) as e:
        await make(lambda r: httpx.Response(500)).search(["x"])
    assert "apikey" not in str(e.value)

    def boom(request):
        raise httpx.ConnectError("down")

    with pytest.raises(ProwlarrError):
        await make(boom).search(["x"])


@pytest.mark.anyio
async def test_missing_api_key_raises():
    client = make(lambda r: httpx.Response(200, json=[]), key="")
    with pytest.raises(ProwlarrError):
        await client.search(["x"])
    with pytest.raises(ProwlarrError):
        await client.resolve(release(download_url="http://x/d"))


@pytest.mark.anyio
async def test_resolve_magnet_returned_as_is():
    def handler(request):
        raise AssertionError("no request expected")

    assert await make(handler).resolve(release(magnet="magnet:?xt=urn:btih:aa")) \
        == "magnet:?xt=urn:btih:aa"


@pytest.mark.anyio
async def test_resolve_magnet_redirect():
    def handler(request):
        return httpx.Response(302, headers={"location": "magnet:?xt=urn:btih:bb"})

    out = await make(handler).resolve(release(download_url="http://p/1/download?apikey=s"))
    assert out == "magnet:?xt=urn:btih:bb"


@pytest.mark.anyio
async def test_resolve_follows_http_redirect_once():
    def handler(request):
        if request.url.host == "p":
            return httpx.Response(302, headers={"location": "http://q/file.torrent"})
        return httpx.Response(200, content=b"d8:announce3:abce")

    out = await make(handler).resolve(release(download_url="http://p/dl"))
    assert out == b"d8:announce3:abce"


@pytest.mark.anyio
async def test_resolve_torrent_bytes():
    def handler(request):
        return httpx.Response(200, content=b"d4:infod4:name1:aee")

    assert await make(handler).resolve(release(download_url="http://p/dl")) \
        == b"d4:infod4:name1:aee"


@pytest.mark.anyio
async def test_resolve_http_magnet_url_with_null_download_url():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(301, headers={"location": "magnet:?xt=urn:btih:cc"})

    out = await make(handler).resolve(
        release(magnet="http://host:9696/1/download?apikey=s&link=x"))
    assert out == "magnet:?xt=urn:btih:cc"
    assert seen and seen[0].startswith("http://host:9696/1/download")


@pytest.mark.anyio
async def test_resolve_info_hash_only_builds_magnet():
    def handler(request):
        raise AssertionError("no request expected")

    out = await make(handler).resolve(release(info_hash=HASH, title="Dune: Part Two"))
    assert out.startswith(f"magnet:?xt=urn:btih:{HASH}&dn=Dune%3A%20Part%20Two")
    assert "&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337%2Fannounce" in out
    assert out.count("&tr=") == 4


@pytest.mark.anyio
async def test_resolve_garbage_with_info_hash_falls_back():
    def handler(request):
        return httpx.Response(200, content=b"<html>nope</html>")

    out = await make(handler).resolve(release(download_url="http://p/dl", info_hash=HASH))
    assert out.startswith(f"magnet:?xt=urn:btih:{HASH}")

    def boom(request):
        raise httpx.ConnectError("down")

    out = await make(boom).resolve(release(download_url="http://p/dl", info_hash=HASH))
    assert out.startswith("magnet:?")


@pytest.mark.anyio
async def test_resolve_garbage_without_info_hash_raises():
    def handler(request):
        return httpx.Response(500, text="oops")

    with pytest.raises(ProwlarrError) as e:
        await make(handler).resolve(release(download_url="http://p/dl?apikey=secret"))
    assert "secret" not in str(e.value)
    with pytest.raises(ProwlarrError):
        await make(handler).resolve(release())
