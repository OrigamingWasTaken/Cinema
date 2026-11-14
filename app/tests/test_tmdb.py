import httpx
import pytest

from cinema.tmdb import MovieCard, TmdbClient, TmdbError

MOVIE = {
    "id": 693134, "imdb_id": "tt15239678", "title": "Dune: Part Two",
    "original_title": "Dune: Part Two", "release_date": "2024-02-27", "runtime": 166,
    "original_language": "en", "overview": "Paul...", "poster_path": "/p.jpg",
    "backdrop_path": "/b.jpg",
}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make(handler, clock=None):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kwargs = {"now": clock} if clock else {}
    return TmdbClient("tok", "fr-FR", http, **kwargs)


@pytest.mark.anyio
async def test_movie_parses_fields():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path.endswith("/1"):
            return httpx.Response(200, json={**MOVIE, "release_date": "", "imdb_id": None})
        return httpx.Response(200, json=MOVIE)

    client = make(handler)
    info = await client.movie(693134)
    assert info.year == 2024 and info.imdb_id == "tt15239678" and info.runtime == 166
    assert info.original_title == "Dune: Part Two" and info.backdrop_path == "/b.jpg"
    assert seen[0].headers["authorization"] == "Bearer tok"
    assert seen[0].url.path == "/3/movie/693134"
    empty = await client.movie(1)
    assert empty.year is None and empty.imdb_id is None


@pytest.mark.anyio
async def test_search_passes_query_and_language():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"results": [
            {"id": 1, "title": "Dune", "release_date": "2021-09-15", "poster_path": None},
            {"id": 2, "title": "Odd", "release_date": "", "poster_path": "/o.jpg",
             "backdrop_path": "/ob.jpg"}]})

    cards = await make(handler).search("dune")
    q = seen[0].url.params
    assert seen[0].url.path == "/3/search/movie"
    assert q["query"] == "dune" and q["language"] == "fr-FR" and q["include_adult"] == "false"
    assert cards == [MovieCard(1, "Dune", 2021, None), MovieCard(2, "Odd", None, "/o.jpg", "/ob.jpg")]


@pytest.mark.anyio
async def test_trending_cached_for_an_hour():
    calls = []
    clock = Clock()

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"results": []})

    client = make(handler, clock)
    await client.trending()
    clock.t = 3599
    await client.trending()
    assert len(calls) == 1
    clock.t = 3601
    await client.trending()
    assert len(calls) == 2
    await client.popular()
    assert len(calls) == 3 and calls[-1].url.path == "/3/movie/popular"


@pytest.mark.anyio
async def test_search_cache_is_per_query_and_ten_minutes():
    calls = []
    clock = Clock()

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"results": []})

    client = make(handler, clock)
    await client.search("a")
    await client.search("b")
    await client.search("a")
    assert len(calls) == 2
    clock.t = 601
    await client.search("a")
    assert len(calls) == 3


@pytest.mark.anyio
async def test_error_raises_tmdb_error_and_is_not_cached():
    state = {"n": 0}

    def handler(request):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(401, json={"status_message": "bad"})
        if state["n"] == 2:
            raise httpx.ConnectError("down")
        return httpx.Response(200, json={"results": []})

    client = make(handler)
    with pytest.raises(TmdbError):
        await client.trending()
    with pytest.raises(TmdbError):
        await client.trending()
    assert await client.trending() == []
