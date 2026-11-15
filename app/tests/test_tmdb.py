import httpx
import pytest

from cinema.tmdb import (CollectionHit, CompanyHit, Genre, MovieCard, Paged, PersonHit,
                         TmdbClient, TmdbError)

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
    await client.search_all("a")
    await client.search_all("b")
    await client.search_all("a")
    assert len(calls) == 8
    clock.t = 601
    await client.search_all("a")
    assert len(calls) == 12


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


def _search_handler(seen, fail=()):
    payloads = {
        "/3/search/movie": {"results": [{"id": 1, "title": "Dune", "release_date": "2021-09-15",
                                         "poster_path": "/d.jpg"}]},
        "/3/search/person": {"results": [
            {"id": 10, "name": "Low", "profile_path": "/l.jpg", "popularity": 1.0,
             "known_for_department": "Acting"},
            {"id": 11, "name": "Sound Guy", "profile_path": "/s.jpg", "popularity": 99.0,
             "known_for_department": "Sound"},
            {"id": 12, "name": "High", "profile_path": "/h.jpg", "popularity": 50.0,
             "known_for_department": "Directing"},
            {"id": 13, "name": "No Photo", "profile_path": None, "popularity": 80.0,
             "known_for_department": "Acting"}]},
        "/3/search/company": {"results": [
            {"id": 20, "name": "Plain", "logo_path": None},
            {"id": 21, "name": "Logo", "logo_path": "/lg.png"}]},
        "/3/search/collection": {"results": [
            {"id": 30, "name": "Dune Collection", "poster_path": "/c.jpg",
             "backdrop_path": None}]},
    }

    def handler(request):
        seen.append(request)
        if request.url.path in fail:
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=payloads[request.url.path])
    return handler


@pytest.mark.anyio
async def test_search_all_groups_and_filters():
    seen = []
    result = await make(_search_handler(seen)).search_all("dune")
    assert result.films == [MovieCard(1, "Dune", 2021, "/d.jpg")]
    assert [p.id for p in result.people] == [12, 10]
    assert result.people[0] == PersonHit(12, "High", "/h.jpg", "Directing")
    assert [c.id for c in result.studios] == [21, 20]
    assert result.studios[0] == CompanyHit(21, "Logo", "/lg.png")
    assert result.collections == [CollectionHit(30, "Dune Collection", "/c.jpg", None)]
    assert len(seen) == 4
    assert seen[0].url.params["include_adult"] == "false"
    for request in seen:
        assert request.url.params["query"] == "dune"
        assert request.url.params["language"] == "fr-FR"


@pytest.mark.anyio
async def test_search_all_keeps_unphotographed_exact_name_match():
    result = await make(_search_handler([])).search_all("no photo")
    assert 13 in [p.id for p in result.people]


@pytest.mark.anyio
async def test_search_all_partial_failure():
    result = await make(_search_handler([], fail={"/3/search/company"})).search_all("dune")
    assert result.films and result.people
    assert result.studios == []


@pytest.mark.anyio
async def test_search_all_partial_result_is_not_cached():
    state = {"movie_ok": False}

    def handler(request):
        if request.url.path == "/3/search/movie":
            if not state["movie_ok"]:
                return httpx.Response(500, text="boom")
            return httpx.Response(200, json={"results": [
                {"id": 1, "title": "Dune", "release_date": "2021-09-15"}]})
        return httpx.Response(200, json={"results": []})

    client = make(handler)
    assert (await client.search_all("dune")).films == []
    state["movie_ok"] = True
    assert [f.id for f in (await client.search_all("dune")).films] == [1]
    assert [f.id for f in (await client.search_all("dune")).films] == [1]


@pytest.mark.anyio
async def test_search_all_all_failing_raises():
    fail = {"/3/search/movie", "/3/search/person", "/3/search/company", "/3/search/collection"}
    with pytest.raises(TmdbError):
        await make(_search_handler([], fail=fail)).search_all("dune")


@pytest.mark.anyio
async def test_person_dedupes_cast_and_director():
    def handler(request):
        if request.url.path == "/3/person/5":
            return httpx.Response(200, json={"id": 5, "name": "Denis", "profile_path": "/p.jpg",
                                             "known_for_department": "Directing"})
        assert request.url.path == "/3/person/5/movie_credits"
        return httpx.Response(200, json={
            "cast": [{"id": 1, "title": "A", "popularity": 5.0, "release_date": "2000-01-01",
                      "poster_path": "/a.jpg"},
                     {"id": 2, "title": "B", "popularity": 9.0, "release_date": "",
                      "poster_path": "/b.jpg"}],
            "crew": [{"id": 1, "title": "A", "popularity": 5.0, "job": "Director",
                      "poster_path": "/a.jpg"},
                     {"id": 3, "title": "C", "popularity": 7.0, "job": "Director",
                      "poster_path": "/c.jpg"},
                     {"id": 4, "title": "D", "popularity": 99.0, "job": "Producer",
                      "poster_path": "/d.jpg"}]})

    person, films = await make(handler).person(5)
    assert person == PersonHit(5, "Denis", "/p.jpg", "Directing")
    assert [f.id for f in films] == [2, 3, 1]


@pytest.mark.anyio
async def test_person_drops_adult_and_posterless_and_caps():
    def film(i, **kw):
        return {"id": i, "title": f"F{i}", "popularity": float(i), "poster_path": "/p.jpg", **kw}

    cast = [film(i) for i in range(1, 71)]
    cast += [film(100, adult=True), film(101, poster_path=None), film(102, poster_path="")]

    def handler(request):
        if request.url.path == "/3/person/5":
            return httpx.Response(200, json={"id": 5, "name": "D", "profile_path": None,
                                             "known_for_department": "Acting"})
        return httpx.Response(200, json={"cast": cast, "crew": []})

    _, films = await make(handler).person(5)
    ids = [f.id for f in films]
    assert len(ids) == 60
    assert ids[0] == 70 and ids[-1] == 11
    assert not {100, 101, 102} & set(ids)


@pytest.mark.anyio
async def test_company_and_genre_paging():
    seen = []

    def handler(request):
        seen.append(request)
        path = request.url.path
        if path == "/3/company/7":
            return httpx.Response(200, json={"id": 7, "name": "A24", "logo_path": "/a.png"})
        if path == "/3/genre/movie/list":
            return httpx.Response(200, json={"genres": [{"id": 12, "name": "Adventure"},
                                                        {"id": 28, "name": "Action"}]})
        assert path == "/3/discover/movie"
        return httpx.Response(200, json={"page": 2, "total_pages": 9000, "results": [
            {"id": 1, "title": "X", "release_date": "2020-01-01", "poster_path": None}]})

    client = make(handler)
    company, paged = await client.company(7, page=2)
    assert company == CompanyHit(7, "A24", "/a.png")
    assert paged == Paged([MovieCard(1, "X", 2020, None)], 2, 500)
    discover = [r for r in seen if r.url.path == "/3/discover/movie"][0]
    assert discover.url.params["with_companies"] == "7"
    assert discover.url.params["page"] == "2"
    assert discover.url.params["sort_by"] == "popularity.desc"

    genre, paged = await client.genre_movies(28, page=3)
    assert genre == Genre(28, "Action") and paged.total_pages == 500
    discover = [r for r in seen if r.url.path == "/3/discover/movie"][1]
    assert discover.url.params["with_genres"] == "28"
    assert discover.url.params["vote_count.gte"] == "50"
    assert discover.url.params["page"] == "3"

    assert await client.genres() == [Genre(12, "Adventure"), Genre(28, "Action")]
    with pytest.raises(TmdbError) as exc:
        await client.genre_movies(999)
    assert exc.value.status == 404


@pytest.mark.anyio
async def test_collection_parts_sorted_by_date():
    def handler(request):
        assert request.url.path == "/3/collection/9"
        return httpx.Response(200, json={"id": 9, "name": "Saga", "poster_path": "/s.jpg",
                                         "backdrop_path": "/sb.jpg", "parts": [
            {"id": 3, "title": "Three", "release_date": "2005-01-01", "poster_path": "/3.jpg"},
            {"id": 4, "title": "Four", "release_date": "", "poster_path": "/4.jpg"},
            {"id": 5, "title": "NoPoster", "release_date": "2001-01-01", "poster_path": None},
            {"id": 1, "title": "One", "release_date": "1999-01-01", "poster_path": "/1.jpg"}]})

    collection, films = await make(handler).collection(9)
    assert collection == CollectionHit(9, "Saga", "/s.jpg", "/sb.jpg")
    assert [f.id for f in films] == [1, 3, 4]
