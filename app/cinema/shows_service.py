"""TV show actions and pages: season state, follow/unfollow, get/remove a season.

Sonarr owns the show (what is monitored, searched and imported); this service drives it
on behalf of the UI and reports each season's state for the show page.
"""
import asyncio
import time
from collections.abc import Callable
from datetime import datetime, timezone

from cinema.shows import ShowsTracker
from cinema.sonarr import SonarrClient, SonarrEpisode, SonarrError, SonarrSeries
from cinema.store import Store
from cinema.tmdb import ShowCard, ShowInfo, TmdbClient, TmdbError

LOOKING_SECONDS = 24 * 3600


class NotAvailableError(Exception):
    """The show has no tvdb id, or Sonarr cannot find it."""


class NotAiringError(Exception):
    """The show is not airing, so there is nothing to follow."""


def _aired_at(ep: SonarrEpisode) -> float | None:
    if not ep.air_date_utc:
        return None
    try:
        dt = datetime.fromisoformat(ep.air_date_utc.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _is_aired(ep: SonarrEpisode, now: float) -> bool:
    at = _aired_at(ep)
    return at is not None and at <= now


def season_state(episodes: list[SonarrEpisode], *, monitored: bool, queued: set[int],
                 requested_at: float | None, now: float, number: int | None = None) -> dict:
    if number is None:
        number = episodes[0].season if episodes else 0
    on_tv = sum(1 for e in episodes if e.has_file)
    aired = sum(1 for e in episodes if _is_aired(e, now))
    downloading = sum(1 for e in episodes if e.id in queued)
    if downloading:
        state = "downloading"
    elif aired == 0:
        state = "upcoming"
    elif on_tv == aired:
        state = "done"
    elif monitored and requested_at is not None and now - requested_at < LOOKING_SECONDS:
        state = "looking"
    elif monitored:
        state = "not_found"
    elif on_tv > 0:
        state = "partial"
    else:
        state = "none"
    return {"number": number, "state": state, "on_tv": on_tv, "total": len(episodes),
            "aired": aired, "downloading": downloading}


def _episode_state(ep: SonarrEpisode, queued: set[int], now: float) -> str:
    if ep.has_file:
        return "on_tv"
    if ep.id in queued:
        return "downloading"
    if not _is_aired(ep, now):
        return "not_aired"
    return "missing"


def _has_files(s: SonarrSeries) -> bool:
    return any(x.episode_file_count > 0 for x in s.seasons)


class ShowsService:
    def __init__(self, store: Store, sonarr: SonarrClient, tmdb: TmdbClient,
                 tracker: ShowsTracker, *, shows_dir: str,
                 now: Callable[[], float] = time.time):
        self.store = store
        self.sonarr = sonarr
        self.tmdb = tmdb
        self.tracker = tracker
        self.shows_dir = shows_dir
        self.now = now
        self._locks: dict[int, asyncio.Lock] = {}

    def _lock(self, tmdb_id: int) -> asyncio.Lock:
        return self._locks.setdefault(tmdb_id, asyncio.Lock())

    async def _existing(self, tvdb_id: int) -> SonarrSeries | None:
        return next((s for s in await self.sonarr.all_series() if s.tvdb_id == tvdb_id), None)

    async def _ensure(self, info: ShowInfo) -> SonarrSeries:
        """The Sonarr series for a show, added (unmonitored) when it is missing."""
        if info.tvdb_id is None:
            raise NotAvailableError("show has no tvdb id")
        existing = await self._existing(info.tvdb_id)
        if existing is not None:
            return existing
        found = await self.sonarr.lookup_tvdb(info.tvdb_id)
        if found is None:
            raise NotAvailableError("sonarr cannot find the show")
        profile = await self.sonarr.quality_profile_id(
            "Cinema FR" if info.original_language == "fr" else "Cinema")
        return await self.sonarr.add_series(
            found, quality_profile_id=profile, root_folder=self.shows_dir,
            series_type="anime" if info.anime else "standard")

    @staticmethod
    def _set_season_monitored(series: SonarrSeries, season: int, monitored: bool) -> list[dict]:
        return [{**s, "monitored": monitored} if s.get("seasonNumber") == season else s
                for s in series.raw.get("seasons") or []]

    async def page(self, tmdb_id: int) -> dict:
        info = await self.tmdb.tv(tmdb_id)
        show = {"id": info.id, "title": info.title, "year": info.year,
                "end_year": info.end_year, "overview": info.overview,
                "poster_path": info.poster_path, "backdrop_path": info.backdrop_path,
                "airing": info.airing}
        unavailable = {"show": show, "available": False, "following": False, "seasons": []}
        if info.tvdb_id is None:
            return unavailable
        series = await self._existing(info.tvdb_id)
        if series is None:
            found = await self.sonarr.lookup_tvdb(info.tvdb_id)
            if found is None:
                return unavailable
            seasons = [{"number": s.number, "state": "none", "on_tv": 0,
                        "total": s.episode_count, "aired": 0, "downloading": 0,
                        "episodes": None}
                       for s in found.seasons if s.number != 0]
            return {"show": show, "available": True, "following": False, "seasons": seasons}

        now = self.now()
        episodes = await self.sonarr.episodes(series.id)
        queued = {q.episode_id for q in await self.sonarr.queue()
                  if q.series_id == series.id}
        seasons = []
        for s in series.seasons:
            if s.number == 0:
                continue
            eps = sorted((e for e in episodes if e.season == s.number),
                         key=lambda e: e.number)
            state = season_state(
                eps, monitored=s.monitored, queued=queued, number=s.number, now=now,
                requested_at=self.store.season_requested_at(tmdb_id, s.number))
            state["episodes"] = [{"number": e.number, "title": e.title,
                                  "air_date": e.air_date_utc,
                                  "state": _episode_state(e, queued, now)} for e in eps]
            seasons.append(state)
        return {"show": show, "available": True,
                "following": series.monitor_new_items == "all", "seasons": seasons}

    async def _get_season(self, tmdb_id: int, series: SonarrSeries,
                          season: int) -> None:
        series = await self.sonarr.update_series(
            series, seasons=self._set_season_monitored(series, season, True))
        ids = [e.id for e in await self.sonarr.episodes(series.id) if e.season == season]
        await self.sonarr.monitor_episodes(ids, True)
        await self.sonarr.season_search(series.id, season)
        self.store.set_season_requested(tmdb_id, season, self.now())
        self.tracker.invalidate(series.id)

    async def get_season(self, tmdb_id: int, season: int) -> dict:
        async with self._lock(tmdb_id):
            info = await self.tmdb.tv(tmdb_id)
            series = await self._ensure(info)
            await self._get_season(tmdb_id, series, season)
        return await self.page(tmdb_id)

    async def follow(self, tmdb_id: int) -> dict:
        async with self._lock(tmdb_id):
            info = await self.tmdb.tv(tmdb_id)
            if info.tvdb_id is None:
                raise NotAvailableError("show has no tvdb id")
            if not info.airing:
                raise NotAiringError("show is not airing")
            series = await self._ensure(info)
            series = await self.sonarr.update_series(series, monitorNewItems="all")
            now = self.now()
            by_season: dict[int, list[SonarrEpisode]] = {}
            for e in await self.sonarr.episodes(series.id):
                if e.season != 0:
                    by_season.setdefault(e.season, []).append(e)
            for number in sorted(by_season):
                eps = by_season[number]
                aired = sum(1 for e in eps if _is_aired(e, now))
                if 0 < aired < len(eps):
                    await self._get_season(tmdb_id, series, number)
                    break
        return await self.page(tmdb_id)

    async def unfollow(self, tmdb_id: int) -> dict:
        async with self._lock(tmdb_id):
            info = await self.tmdb.tv(tmdb_id)
            series = await self._existing(info.tvdb_id) if info.tvdb_id else None
            if series is not None:
                await self.sonarr.update_series(series, monitorNewItems="none")
        return await self.page(tmdb_id)

    async def remove_season(self, tmdb_id: int, season: int) -> dict:
        async with self._lock(tmdb_id):
            info = await self.tmdb.tv(tmdb_id)
            series = await self._existing(info.tvdb_id) if info.tvdb_id else None
            if series is not None:
                await self.sonarr.update_series(
                    series, seasons=self._set_season_monitored(series, season, False))
                eps = [e for e in await self.sonarr.episodes(series.id) if e.season == season]
                if eps:
                    await self.sonarr.monitor_episodes([e.id for e in eps], False)
                files = [e.episode_file_id for e in eps if e.episode_file_id]
                if files:
                    await self.sonarr.delete_episode_files(files)
                for q in await self.sonarr.queue():
                    if q.series_id == series.id and q.season == season:
                        await self.sonarr.remove_from_queue(q.id, blocklist=False)
                self.store.clear_season_requested(tmdb_id, season)
                self.tracker.invalidate(series.id)
        return await self.page(tmdb_id)

    async def _card(self, tmdb_id: int) -> ShowCard | None:
        try:
            info = await self.tmdb.tv(tmdb_id)
        except TmdbError:
            return None
        return ShowCard(id=info.id, title=info.title, year=info.year,
                        poster_path=info.poster_path, backdrop_path=info.backdrop_path)

    async def home(self) -> dict:
        out: dict = {"yours": [], "library": [], "trending": [], "popular": []}
        try:
            series = [s for s in await self.sonarr.all_series() if s.tmdb_id]
        except SonarrError:
            out["sonarr_error"] = True
            series = []
        yours = [s for s in series if s.monitor_new_items == "all"
                 or any(x.monitored for x in s.seasons) or _has_files(s)]
        ids = list(dict.fromkeys(s.tmdb_id for s in yours))
        cards = dict(zip(ids, await asyncio.gather(*(self._card(i) for i in ids))))
        out["yours"] = [c for s in yours if (c := cards[s.tmdb_id])]
        out["library"] = [c for s in yours if _has_files(s) and (c := cards[s.tmdb_id])]
        for key, fetch in (("trending", self.tmdb.tv_trending),
                           ("popular", self.tmdb.tv_popular)):
            try:
                out[key] = await fetch()
            except TmdbError:
                out["catalog_error"] = True
        return out
