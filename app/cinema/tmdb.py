import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

BASE_URL = "https://api.themoviedb.org/3"
HOUR = 3600.0
SEARCH_TTL = 600.0


class TmdbError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class MovieCard:
    id: int
    title: str
    year: int | None
    poster_path: str | None
    backdrop_path: str | None = None


@dataclass(frozen=True)
class MovieInfo:
    id: int
    imdb_id: str | None
    title: str
    original_title: str
    year: int | None
    runtime: int | None
    original_language: str
    overview: str
    poster_path: str | None
    backdrop_path: str | None


@dataclass(frozen=True)
class ShowCard:
    id: int
    title: str
    year: int | None
    poster_path: str | None
    backdrop_path: str | None = None


@dataclass(frozen=True)
class ShowInfo:
    id: int
    title: str
    year: int | None
    end_year: int | None
    overview: str
    original_language: str
    poster_path: str | None
    backdrop_path: str | None
    airing: bool
    anime: bool
    tvdb_id: int | None
    # (number, name, air_year) for TMDB seasons 1.., and (season, episode, "YYYY-MM-DD").
    seasons: tuple[tuple[int, str, int | None], ...] = ()
    next_episode: tuple[int, int, str] | None = None


@dataclass(frozen=True)
class PersonHit:
    id: int
    name: str
    profile_path: str | None
    known_for_department: str | None


@dataclass(frozen=True)
class CompanyHit:
    id: int
    name: str
    logo_path: str | None


@dataclass(frozen=True)
class CollectionHit:
    id: int
    name: str
    poster_path: str | None
    backdrop_path: str | None = None


@dataclass(frozen=True)
class Genre:
    id: int
    name: str


@dataclass(frozen=True)
class SearchAll:
    films: list[MovieCard]
    people: list[PersonHit]
    studios: list[CompanyHit]
    collections: list[CollectionHit]
    shows: list[ShowCard]


@dataclass(frozen=True)
class Paged:
    films: list[MovieCard]
    page: int
    total_pages: int


MAX_PAGES = 500
PERSON_FILM_CAP = 60


def _year(release_date) -> int | None:
    if isinstance(release_date, str) and len(release_date) >= 4 and release_date[:4].isdigit():
        return int(release_date[:4])
    return None


def _card(item: dict) -> MovieCard:
    return MovieCard(item["id"], item["title"], _year(item.get("release_date")),
                     item.get("poster_path"), item.get("backdrop_path"))


def _show_card(item: dict) -> ShowCard:
    return ShowCard(item["id"], item["name"], _year(item.get("first_air_date")),
                    item.get("poster_path"), item.get("backdrop_path"))


def _person(item: dict) -> PersonHit:
    return PersonHit(item["id"], item["name"], item.get("profile_path"),
                     item.get("known_for_department"))


def _company(item: dict) -> CompanyHit:
    return CompanyHit(item["id"], item["name"], item.get("logo_path"))


def _collection(item: dict) -> CollectionHit:
    return CollectionHit(item["id"], item["name"], item.get("poster_path"),
                         item.get("backdrop_path"))


def _paged(data: dict, page: int) -> Paged:
    return Paged([_card(item) for item in data.get("results", [])], page,
                 min(data.get("total_pages") or 1, MAX_PAGES))


class TmdbClient:
    def __init__(self, token: str, language: str, http: httpx.AsyncClient,
                 now: Callable[[], float] = time.monotonic):
        self._headers = {"Authorization": f"Bearer {token}"}
        self._language = language
        self._http = http
        self._now = now
        self._cache: dict[tuple, tuple[float, object]] = {}

    async def _get(self, path: str, **params) -> dict:
        try:
            resp = await self._http.get(
                f"{BASE_URL}{path}", headers=self._headers,
                params={**params, "language": self._language})
        except httpx.HTTPError as exc:
            raise TmdbError(f"tmdb unreachable: {exc}") from exc
        if not resp.is_success:
            raise TmdbError(f"tmdb error {resp.status_code}: {resp.text[:200]}", resp.status_code)
        try:
            return resp.json()
        except ValueError as exc:
            raise TmdbError("tmdb returned invalid JSON") from exc

    async def _cached(self, key: tuple, ttl: float, fetch):
        hit = self._cache.get(key)
        now = self._now()
        if hit and now - hit[0] < ttl:
            return hit[1]
        value = await fetch()
        self._cache[key] = (now, value)
        return value

    async def _cached_if(self, key: tuple, ttl: float, fetch):
        """Like _cached, but fetch returns (value, cacheable)."""
        hit = self._cache.get(key)
        now = self._now()
        if hit and now - hit[0] < ttl:
            return hit[1]
        value, cacheable = await fetch()
        if cacheable:
            self._cache[key] = (now, value)
        return value

    async def _cards(self, path: str, **params) -> list[MovieCard]:
        data = await self._get(path, **params)
        return [_card(item) for item in data.get("results", [])]

    async def trending(self) -> list[MovieCard]:
        return await self._cached(("trending",), HOUR,
                                  lambda: self._cards("/trending/movie/week"))

    async def popular(self) -> list[MovieCard]:
        return await self._cached(("popular",), HOUR, lambda: self._cards("/movie/popular"))

    async def movie(self, tmdb_id: int) -> MovieInfo:
        async def fetch() -> MovieInfo:
            d = await self._get(f"/movie/{tmdb_id}")
            return MovieInfo(
                d["id"], d.get("imdb_id") or None, d["title"],
                d.get("original_title") or d["title"], _year(d.get("release_date")),
                d.get("runtime") or None, d.get("original_language") or "",
                d.get("overview") or "", d.get("poster_path"), d.get("backdrop_path"))
        return await self._cached(("movie", tmdb_id), HOUR, fetch)

    async def _show_cards(self, path: str) -> list[ShowCard]:
        data = await self._get(path)
        return [_show_card(item) for item in data.get("results", [])]

    async def tv_trending(self) -> list[ShowCard]:
        return await self._cached(("tv_trending",), HOUR,
                                  lambda: self._show_cards("/trending/tv/week"))

    async def tv_popular(self) -> list[ShowCard]:
        return await self._cached(("tv_popular",), HOUR,
                                  lambda: self._show_cards("/tv/popular"))

    async def tv(self, tmdb_id: int) -> ShowInfo:
        async def fetch() -> ShowInfo:
            d = await self._get(f"/tv/{tmdb_id}", append_to_response="external_ids")
            airing = bool(d.get("in_production")) or d.get("status") == "Returning Series"
            anime = (16 in [g.get("id") for g in d.get("genres") or []]
                     and "JP" in (d.get("origin_country") or []))
            seasons = tuple(
                (n, x.get("name") or "", _year(x.get("air_date")))
                for x in d.get("seasons") or []
                if isinstance(n := x.get("season_number"), int) and n >= 1)
            nxt = d.get("next_episode_to_air") or {}
            next_episode = None
            if (isinstance(nxt.get("season_number"), int)
                    and isinstance(nxt.get("episode_number"), int)
                    and isinstance(nxt.get("air_date"), str) and nxt["air_date"]):
                next_episode = (nxt["season_number"], nxt["episode_number"], nxt["air_date"])
            return ShowInfo(
                d["id"], d["name"], _year(d.get("first_air_date")),
                None if airing else _year(d.get("last_air_date")),
                d.get("overview") or "", d.get("original_language") or "",
                d.get("poster_path"), d.get("backdrop_path"), airing, anime,
                (d.get("external_ids") or {}).get("tvdb_id") or None,
                seasons, next_episode)
        return await self._cached(("tv", tmdb_id), HOUR, fetch)

    async def search_all(self, query: str) -> SearchAll:
        async def fetch() -> tuple[SearchAll, bool]:
            params = {"query": query, "include_adult": "false"}
            results = await asyncio.gather(
                self._get("/search/movie", **params),
                self._get("/search/person", **params),
                self._get("/search/company", **params),
                self._get("/search/collection", **params),
                self._get("/search/tv", **params),
                return_exceptions=True)
            for r in results:
                if isinstance(r, asyncio.CancelledError):
                    raise r
            failures = [r for r in results if isinstance(r, BaseException)]
            if len(failures) == len(results):
                raise next((f for f in failures if isinstance(f, TmdbError)), failures[0])
            movies, people, studios, collections, shows = (
                {} if isinstance(r, BaseException) else r for r in results)
            wanted = query.casefold()
            ranked = sorted(
                (p for p in people.get("results", [])
                 if p.get("known_for_department") in ("Acting", "Directing")),
                key=lambda p: p.get("popularity") or 0, reverse=True)[:12]
            kept = [p for p in ranked
                    if p.get("profile_path") or p["name"].casefold() == wanted]
            company_items = studios.get("results", [])
            company_items = ([c for c in company_items if c.get("logo_path")]
                             + [c for c in company_items if not c.get("logo_path")])[:6]
            found = SearchAll(
                [_card(m) for m in movies.get("results", [])],
                [_person(p) for p in kept],
                [_company(c) for c in company_items],
                [_collection(c) for c in collections.get("results", [])[:6]],
                [_show_card(s) for s in shows.get("results", [])])
            # A partial result is returned but never cached, so a retry can fill the gaps.
            return found, not failures
        return await self._cached_if(("search_all", query), SEARCH_TTL, fetch)

    async def person(self, person_id: int) -> tuple[PersonHit, list[MovieCard]]:
        async def fetch() -> tuple[PersonHit, list[MovieCard]]:
            info, credits = await asyncio.gather(
                self._get(f"/person/{person_id}"),
                self._get(f"/person/{person_id}/movie_credits"))
            items: dict[int, dict] = {}
            for item in credits.get("cast", []):
                items.setdefault(item["id"], item)
            for item in credits.get("crew", []):
                if item.get("job") == "Director":
                    items.setdefault(item["id"], item)
            shown = [i for i in items.values()
                     if not i.get("adult") and i.get("poster_path")]
            ordered = sorted(shown, key=lambda i: i.get("popularity") or 0,
                             reverse=True)[:PERSON_FILM_CAP]
            return _person(info), [_card(i) for i in ordered]
        return await self._cached(("person", person_id), HOUR, fetch)

    async def company(self, company_id: int, page: int = 1) -> tuple[CompanyHit, Paged]:
        async def fetch() -> tuple[CompanyHit, Paged]:
            info, data = await asyncio.gather(
                self._get(f"/company/{company_id}"),
                self._get("/discover/movie", with_companies=company_id,
                          sort_by="popularity.desc", page=page))
            return _company(info), _paged(data, page)
        return await self._cached(("company", company_id, page), HOUR, fetch)

    async def collection(self, collection_id: int) -> tuple[CollectionHit, list[MovieCard]]:
        async def fetch() -> tuple[CollectionHit, list[MovieCard]]:
            d = await self._get(f"/collection/{collection_id}")
            parts = sorted((p for p in d.get("parts", []) if p.get("poster_path")),
                           key=lambda p: (not p.get("release_date"), p.get("release_date") or ""))
            return _collection(d), [_card(p) for p in parts]
        return await self._cached(("collection", collection_id), HOUR, fetch)

    async def genres(self) -> list[Genre]:
        async def fetch() -> list[Genre]:
            d = await self._get("/genre/movie/list")
            return [Genre(g["id"], g["name"]) for g in d.get("genres", [])]
        return await self._cached(("genres",), HOUR, fetch)

    async def genre_movies(self, genre_id: int, page: int = 1) -> tuple[Genre, Paged]:
        async def fetch() -> tuple[Genre, Paged]:
            genre = next((g for g in await self.genres() if g.id == genre_id), None)
            if genre is None:
                raise TmdbError("unknown genre", status=404)
            data = await self._get("/discover/movie", with_genres=genre_id,
                                   sort_by="popularity.desc", page=page,
                                   **{"vote_count.gte": 50})
            return genre, _paged(data, page)
        return await self._cached(("genre_movies", genre_id, page), HOUR, fetch)
