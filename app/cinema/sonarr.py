from collections.abc import Callable
from dataclasses import dataclass

import httpx


class SonarrError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SonarrSeason:
    number: int
    monitored: bool
    episode_count: int
    episode_file_count: int


@dataclass(frozen=True)
class SonarrSeries:
    id: int | None  # None for a lookup result
    tvdb_id: int
    tmdb_id: int | None
    title: str
    year: int | None
    status: str
    series_type: str
    monitor_new_items: str
    seasons: list[SonarrSeason]
    raw: dict


@dataclass(frozen=True)
class SonarrEpisode:
    id: int
    season: int
    number: int
    absolute: int | None
    title: str
    air_date_utc: str | None
    monitored: bool
    has_file: bool
    episode_file_id: int | None


@dataclass(frozen=True)
class SonarrEpisodeFile:
    id: int
    season: int
    path: str
    size: int


@dataclass(frozen=True)
class QueueItem:
    id: int
    download_id: str
    series_id: int
    episode_id: int
    season: int
    episode: int
    absolute: int | None
    status: str
    tracked_state: str


def _series(d: dict) -> SonarrSeries:
    seasons = []
    for s in d.get("seasons") or []:
        stats = s.get("statistics") or {}
        seasons.append(SonarrSeason(
            number=s["seasonNumber"],
            monitored=bool(s.get("monitored")),
            episode_count=stats.get("episodeCount") or 0,
            episode_file_count=stats.get("episodeFileCount") or 0,
        ))
    return SonarrSeries(
        id=d.get("id"),
        tvdb_id=d.get("tvdbId") or 0,
        tmdb_id=d.get("tmdbId") or None,
        title=d.get("title") or "",
        year=d.get("year") or None,
        status=d.get("status") or "",
        series_type=d.get("seriesType") or "standard",
        monitor_new_items=d.get("monitorNewItems") or "all",
        seasons=seasons,
        raw=d,
    )


def _episode(d: dict) -> SonarrEpisode:
    return SonarrEpisode(
        id=d["id"],
        season=d.get("seasonNumber") or 0,
        number=d.get("episodeNumber") or 0,
        absolute=d.get("absoluteEpisodeNumber"),
        title=d.get("title") or "",
        air_date_utc=d.get("airDateUtc"),
        monitored=bool(d.get("monitored")),
        has_file=bool(d.get("hasFile")),
        episode_file_id=d.get("episodeFileId") or None,
    )


def _queue_item(r: dict) -> QueueItem | None:
    if not r.get("downloadId") or not r.get("episodeId"):
        return None
    ep = r.get("episode") or {}
    season = ep.get("seasonNumber")
    if season is None:
        season = r.get("seasonNumber") or 0
    return QueueItem(
        id=r["id"],
        download_id=r["downloadId"].lower(),
        series_id=r.get("seriesId") or 0,
        episode_id=r["episodeId"],
        season=season,
        episode=ep.get("episodeNumber") or 0,
        absolute=ep.get("absoluteEpisodeNumber"),
        status=r.get("status") or "",
        tracked_state=r.get("trackedDownloadState") or "",
    )


class SonarrClient:
    def __init__(self, base_url: str, api_key: str | Callable[[], str],
                 http: httpx.AsyncClient):
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._http = http

    def _api_key(self) -> str:
        key = self._key() if callable(self._key) else self._key
        if not key:
            raise SonarrError("sonarr api key is not configured")
        return key

    async def _request(self, method: str, path: str, **kwargs):
        headers = {"X-Api-Key": self._api_key()}
        try:
            resp = await self._http.request(method, self._base + path,
                                            headers=headers, **kwargs)
        except httpx.HTTPError as e:
            raise SonarrError(f"sonarr unreachable: {type(e).__name__}") from None
        if not resp.is_success:
            raise SonarrError(f"sonarr {method} {path} failed: HTTP {resp.status_code}",
                              resp.status_code)
        if not resp.content:
            return None
        try:
            return resp.json()
        except ValueError:
            raise SonarrError("sonarr returned invalid JSON", resp.status_code) from None

    async def lookup_tvdb(self, tvdb_id: int) -> SonarrSeries | None:
        data = await self._request("GET", "/api/v3/series/lookup",
                                   params={"term": f"tvdb:{tvdb_id}"})
        return _series(data[0]) if data else None

    async def all_series(self) -> list[SonarrSeries]:
        return [_series(d) for d in await self._request("GET", "/api/v3/series")]

    async def add_series(self, found: SonarrSeries, *, quality_profile_id: int,
                         root_folder: str, series_type: str,
                         monitor: str = "none", monitored_seasons: frozenset[int] = frozenset(),
                         search_missing: bool = False,
                         monitor_new_items: str = "none") -> SonarrSeries:
        """Add a series. Sonarr creates its episodes later (an async refresh) and only then
        applies `addOptions`: `monitor` ("none", "future", or "skip" to keep the season
        flags set here) and the missing-episode search. So choices made at add time go
        here, not into follow-up calls."""
        body = {
            **found.raw,
            "qualityProfileId": quality_profile_id,
            "rootFolderPath": root_folder,
            "seriesType": series_type,
            "monitored": True,
            "monitorNewItems": monitor_new_items,
            "seasonFolder": True,
            "seasons": [{**s, "monitored": s.get("seasonNumber") in monitored_seasons}
                        for s in found.raw.get("seasons") or []],
            "addOptions": {"monitor": monitor, "searchForMissingEpisodes": search_missing,
                           "searchForCutoffUnmetEpisodes": False},
        }
        return _series(await self._request("POST", "/api/v3/series", json=body))

    async def update_series(self, series: SonarrSeries, **raw_changes) -> SonarrSeries:
        body = {**series.raw, **raw_changes}
        return _series(await self._request("PUT", f"/api/v3/series/{series.id}", json=body))

    async def episodes(self, series_id: int) -> list[SonarrEpisode]:
        data = await self._request("GET", "/api/v3/episode", params={"seriesId": series_id})
        return [_episode(d) for d in data]

    async def monitor_episodes(self, ids: list[int], monitored: bool) -> None:
        await self._request("PUT", "/api/v3/episode/monitor",
                            json={"episodeIds": ids, "monitored": monitored})

    async def season_search(self, series_id: int, season: int) -> None:
        await self._request("POST", "/api/v3/command",
                            json={"name": "SeasonSearch", "seriesId": series_id,
                                  "seasonNumber": season})

    async def episode_files(self, series_id: int) -> list[SonarrEpisodeFile]:
        data = await self._request("GET", "/api/v3/episodefile",
                                   params={"seriesId": series_id})
        return [SonarrEpisodeFile(id=d["id"], season=d.get("seasonNumber") or 0,
                                  path=d.get("path") or "", size=d.get("size") or 0)
                for d in data]

    async def delete_episode_files(self, ids: list[int]) -> None:
        await self._request("DELETE", "/api/v3/episodefile/bulk",
                            json={"episodeFileIds": ids})

    async def queue(self) -> list[QueueItem]:
        data = await self._request("GET", "/api/v3/queue",
                                   params={"page": 1, "pageSize": 1000,
                                           "includeEpisode": "true"})
        items = (_queue_item(r) for r in (data or {}).get("records") or [])
        return [i for i in items if i is not None]

    async def remove_from_queue(self, queue_id: int, *, blocklist: bool) -> None:
        """Remove a queue record's whole download. A 404 means it is already gone
        (imported, or removed with another record of the same download)."""
        try:
            await self._request("DELETE", f"/api/v3/queue/{queue_id}",
                                params={"removeFromClient": "true",
                                        "blocklist": "true" if blocklist else "false",
                                        "skipRedownload": "false"})
        except SonarrError as e:
            if e.status != 404:
                raise

    async def quality_profile_id(self, name: str) -> int:
        for p in await self._request("GET", "/api/v3/qualityprofile"):
            if p.get("name") == name:
                return p["id"]
        raise SonarrError(f"sonarr quality profile {name!r} not found", 404)
