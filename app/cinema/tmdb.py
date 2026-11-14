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


def _year(release_date) -> int | None:
    if isinstance(release_date, str) and len(release_date) >= 4 and release_date[:4].isdigit():
        return int(release_date[:4])
    return None


def _card(item: dict) -> MovieCard:
    return MovieCard(item["id"], item["title"], _year(item.get("release_date")),
                     item.get("poster_path"), item.get("backdrop_path"))


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

    async def _cards(self, path: str, **params) -> list[MovieCard]:
        data = await self._get(path, **params)
        return [_card(item) for item in data.get("results", [])]

    async def trending(self) -> list[MovieCard]:
        return await self._cached(("trending",), HOUR,
                                  lambda: self._cards("/trending/movie/week"))

    async def popular(self) -> list[MovieCard]:
        return await self._cached(("popular",), HOUR, lambda: self._cards("/movie/popular"))

    async def search(self, query: str) -> list[MovieCard]:
        return await self._cached(
            ("search", query), SEARCH_TTL,
            lambda: self._cards("/search/movie", query=query, include_adult="false"))

    async def movie(self, tmdb_id: int) -> MovieInfo:
        async def fetch() -> MovieInfo:
            d = await self._get(f"/movie/{tmdb_id}")
            return MovieInfo(
                d["id"], d.get("imdb_id") or None, d["title"],
                d.get("original_title") or d["title"], _year(d.get("release_date")),
                d.get("runtime") or None, d.get("original_language") or "",
                d.get("overview") or "", d.get("poster_path"), d.get("backdrop_path"))
        return await self._cached(("movie", tmdb_id), HOUR, fetch)
