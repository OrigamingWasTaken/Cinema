"""TV shows: which episode files exist, merged from Sonarr's queue, rqbit and Sonarr's files.

Sonarr grabs releases and drives rqbit; the tracker is the cinema app's view of them:
in-progress episodes are streamed from rqbit, imported ones are served from disk, and
finished torrents are paused, then removed once Sonarr has imported (hardlinked) them.
"""
import asyncio
import collections
import functools
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

import httpx
from guessit import guessit

from cinema.downloads import stalled_since
from cinema.rqbit import (RqbitClient, RqbitError, TorrentFile, TorrentStats, TorrentSummary,
                          is_video_file)
from cinema.sonarr import (QueueItem, SonarrClient, SonarrEpisode, SonarrEpisodeFile,
                           SonarrError, SonarrSeries)
from cinema.store import ShowDownload, Store, library_name
from cinema.webdav import READY_BYTES

log = logging.getLogger(__name__)

SERIES_REREAD_SECONDS = 60
DELETE_AFTER_FINISH_SECONDS = 60
FAILED_KEEP_SECONDS = 24 * 3600
LISTED_STATES = ("ready", "done")
_REFRESH_ERRORS = (SonarrError, RqbitError, httpx.HTTPError, KeyError, TypeError, ValueError)


@dataclass(frozen=True)
class EpisodeEntry:
    series_id: int
    show_tmdb_id: int | None
    title: str
    year: int | None
    season: int
    episode: int
    last_episode: int
    size: int
    progress_bytes: int
    mtime: float
    path: Path | None  # set when served from disk
    torrent_id: int | None  # torrent_id/file_index: set when streamed from rqbit
    file_index: int | None
    download_bps: float
    eta_s: int | None
    state: str  # "preparing" | "ready" | "done" | "failed"
    ext: str = ".mkv"  # extension of the file the entry comes from

    @property
    def dirname(self) -> str:
        return library_name(self.title, self.year)

    @property
    def season_dir(self) -> str:
        return f"Season {self.season:02d}"

    @property
    def filename(self) -> str:
        code = f"S{self.season:02d}E{self.episode:02d}"
        if self.last_episode != self.episode:
            code += f"-E{self.last_episode:02d}"
        return f"{library_name(self.title, None)} - {code}{self.ext}"


def _ints(value) -> list[int] | None:
    values = value if isinstance(value, list) else [value]
    if not values or not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return None
    return values


@functools.lru_cache(maxsize=4096)
def _guess(basename: str) -> tuple[tuple[int, ...] | None, tuple[int, ...]] | None:
    """(seasons or None, episodes) guessed from a file name, or None without episodes."""
    try:
        g = guessit(basename, {"type": "episode"})
    except Exception:  # guessit can choke on odd names: no confident match then
        log.debug("guessit failed on %r", basename, exc_info=True)
        return None
    episodes = _ints(g.get("episode"))
    if not episodes:
        return None
    seasons = _ints(g.get("season")) if g.get("season") is not None else None
    return (tuple(seasons) if seasons else None), tuple(episodes)


def match_files(files: list[TorrentFile], items: list[QueueItem], *,
                anime: bool) -> dict[int, list[QueueItem]]:
    """Map a torrent file index to the queue items (episodes) it holds.

    Only video, non-sample files whose every guessed episode is among `items` match.
    When files claim the same episode, the largest one keeps it.
    """
    item_seasons = {i.season for i in items}
    out: dict[int, list[QueueItem]] = {}
    claimed: set[int] = set()
    for f in sorted(files, key=lambda f: (-f.length, f.index)):
        if not is_video_file(f):
            continue
        guess = _guess(PurePosixPath(f.path).name)
        if guess is None:
            continue
        seasons, episodes = guess
        if seasons is None:
            if anime:
                by_number = {i.absolute: i for i in items if i.absolute is not None}
            elif len(item_seasons) == 1:
                by_number = {i.episode: i for i in items}
            else:
                continue
        elif len(seasons) == 1:
            by_number = {i.episode: i for i in items if i.season == seasons[0]}
        else:
            continue
        if not all(e in by_number for e in episodes):
            continue
        matched = [by_number[e] for e in sorted(set(episodes))]
        ids = {i.episode_id for i in matched}
        if ids & claimed:
            continue
        claimed |= ids
        out[f.index] = matched
    return dict(sorted(out.items()))


def _mtime(path: Path, fallback: float) -> float:
    try:
        return os.stat(path).st_mtime
    except OSError:
        return fallback


def _episode_keys(e: EpisodeEntry) -> set[tuple[int, int, int]]:
    return {(e.series_id, e.season, n) for n in range(e.episode, e.last_episode + 1)}


class ShowsTracker:
    def __init__(self, store: Store, sonarr: SonarrClient, rqbit: RqbitClient,
                 now: Callable[[], float] = time.time, watch_started: float | None = None):
        self.store = store
        self.sonarr = sonarr
        self.rqbit = rqbit
        self._now = now
        self.watch_started = watch_started
        self.entries: list[EpisodeEntry] = []
        self.failed: list[EpisodeEntry] = []
        self.open_streams: collections.Counter[int] = collections.Counter()
        self.ok = False  # no successful refresh yet
        self._files: dict[tuple[int, str], list[TorrentFile]] = {}
        self._series_data: dict[int, tuple[list[SonarrEpisode], list[SonarrEpisodeFile]]] = {}
        self._read_at: dict[int, float] = {}
        self._invalid: set[int] = set()

    def invalidate(self, series_id: int) -> None:
        self._invalid.add(series_id)

    def listed(self) -> list[EpisodeEntry]:
        """The entries WebDAV lists: ready to stream, or done."""
        return [e for e in self.entries if e.state in LISTED_STATES]

    async def refresh(self) -> None:
        try:
            entries, failed = await self._refresh(self._now())
        except _REFRESH_ERRORS as e:
            self.ok = False
            log.warning("shows refresh failed, keeping the last snapshot: %s: %s",
                        type(e).__name__, e)
            return
        except Exception:
            self.ok = False
            log.exception("shows refresh failed, keeping the last snapshot")
            return
        self.entries, self.failed, self.ok = entries, failed, True

    async def _refresh(self, now: float) -> tuple[list[EpisodeEntry], list[EpisodeEntry]]:
        queue = await self.sonarr.queue()
        series = {s.id: s for s in await self.sonarr.all_series() if s.id is not None}
        torrents = {t.info_hash.lower(): t for t in await self.rqbit.list_torrents()}
        rows = {r.info_hash: r for r in self.store.show_downloads()}
        groups: dict[str, list[QueueItem]] = {}
        for i in queue:
            groups.setdefault(i.download_id, []).append(i)
        self._files = {k: v for k, v in self._files.items() if k[1] in torrents}

        # Queued torrents: progress, finish (pause), stall (blocklist).
        streamed: list[EpisodeEntry] = []
        for h, items in groups.items():
            t = torrents.get(h)
            row = rows.get(h)
            if t is None or (row is not None and row.status == "failed"):
                continue
            files = await self._torrent_files(h, t)
            stats = await self.rqbit.stats(t.id)
            row = rows[h] = await self._track(row, h, t, items, stats, now)
            s = series.get(items[0].series_id)
            if row.status != "failed" and s is not None:
                streamed += self._streamed(s, row, t, files, items, stats)

        # Torrents that left the queue before we saw them finish must still stop uploading.
        for h, row in list(rows.items()):
            if h in groups or row.status != "downloading":
                continue
            t = torrents.get(h)
            if t is None:
                self.store.delete_show_download(h)
                del rows[h]
                continue
            stats = await self.rqbit.stats(t.id)
            if stats.finished:
                await self._pause_finished(t, stats)
                rows[h] = replace(row, torrent_id=t.id, status="finished", finished_at=now)
                self.store.upsert_show_download(rows[h])

        await self._read_series(series, {i.series_id for i in queue}, now)

        # A file on disk wins over a queue entry for the same episode.
        entries = self._on_disk(series, now)
        covered = set().union(*(_episode_keys(e) for e in entries))
        for e in streamed:
            keys = _episode_keys(e)
            if keys & covered:
                continue
            covered |= keys
            entries.append(e)
        entries.sort(key=lambda e: (e.dirname, e.season, e.episode))

        await self._delete_imported(rows, groups, torrents, now)
        failed = self._failed(rows, groups, series, now)
        return entries, failed

    async def _torrent_files(self, h: str, t: TorrentSummary) -> list[TorrentFile]:
        key = (t.id, h)
        if key not in self._files:
            self._files[key] = await self.rqbit.files(t.id)
        return self._files[key]

    async def _pause_finished(self, t: TorrentSummary, stats: TorrentStats) -> None:
        if stats.state != "paused":
            await self.rqbit.pause(t.id)

    async def _track(self, row: ShowDownload | None, h: str, t: TorrentSummary,
                     items: list[QueueItem], stats: TorrentStats, now: float) -> ShowDownload:
        episode_ids = {i.episode_id for i in items}
        if row is None:
            row = ShowDownload(info_hash=h, torrent_id=t.id, series_id=items[0].series_id,
                               episode_ids=sorted(episode_ids), status="downloading",
                               progress_bytes=stats.progress_bytes, added_at=now,
                               last_progress_at=now, finished_at=None)
        else:
            grew = stats.progress_bytes > row.progress_bytes
            row = replace(row, torrent_id=t.id,
                          episode_ids=sorted(episode_ids | set(row.episode_ids)),
                          progress_bytes=stats.progress_bytes,
                          last_progress_at=now if grew else row.last_progress_at)
        if stats.finished:
            await self._pause_finished(t, stats)
            if row.status != "finished":
                row = replace(row, status="finished", finished_at=now)
        elif (row.status == "downloading" and stats.state == "live" and stats.live_peers == 0
              and stalled_since(row.last_progress_at, now, self.watch_started)):
            log.warning("show torrent %s stalled: blocklisting it in Sonarr", t.id)
            await self.sonarr.remove_from_queue(items[0].id, blocklist=True)
            row = replace(row, status="failed", finished_at=now)
        self.store.upsert_show_download(row)
        return row

    def _streamed(self, s: SonarrSeries, row: ShowDownload, t: TorrentSummary,
                  files: list[TorrentFile], items: list[QueueItem],
                  stats: TorrentStats) -> list[EpisodeEntry]:
        by_index = {f.index: f for f in files}
        out = []
        for idx, matched in match_files(files, items, anime=s.series_type == "anime").items():
            season = matched[0].season
            if season == 0 or any(i.season != season for i in matched):
                continue
            f = by_index[idx]
            if stats.finished:  # finished but not imported yet
                progress, state, bps, eta = f.length, "done", 0.0, None
            else:
                progress = stats.file_progress[idx] if idx < len(stats.file_progress) else 0
                state = "ready" if progress >= READY_BYTES else "preparing"
                bps, eta = stats.download_bps, stats.eta_s
            out.append(EpisodeEntry(
                series_id=s.id, show_tmdb_id=s.tmdb_id, title=s.title, year=s.year,
                season=season, episode=matched[0].episode, last_episode=matched[-1].episode,
                size=f.length, progress_bytes=progress, mtime=row.added_at, path=None,
                torrent_id=t.id, file_index=idx, download_bps=bps, eta_s=eta, state=state,
                ext=PurePosixPath(f.path).suffix.lower(),
            ))
        return out

    async def _read_series(self, series: dict[int, SonarrSeries], active: set[int],
                           now: float) -> None:
        for sid in list(self._series_data):
            if sid not in series:
                del self._series_data[sid]
                self._read_at.pop(sid, None)
        for sid in series:
            last = self._read_at.get(sid)
            if (sid in active or sid in self._invalid or last is None
                    or now - last >= SERIES_REREAD_SECONDS):
                episodes = await self.sonarr.episodes(sid)
                files = await self.sonarr.episode_files(sid)
                self._series_data[sid] = (episodes, files)
                self._read_at[sid] = now
                self._invalid.discard(sid)

    def _on_disk(self, series: dict[int, SonarrSeries], now: float) -> list[EpisodeEntry]:
        out = []
        for sid, (episodes, files) in self._series_data.items():
            s = series[sid]
            by_file: dict[int, list[SonarrEpisode]] = {}
            for e in episodes:
                if e.episode_file_id:
                    by_file.setdefault(e.episode_file_id, []).append(e)
            for f in files:
                covered = sorted(by_file.get(f.id, []), key=lambda e: e.number)
                if not covered or covered[0].season == 0 \
                        or any(e.season != covered[0].season for e in covered):
                    continue
                path = Path(f.path)
                out.append(EpisodeEntry(
                    series_id=sid, show_tmdb_id=s.tmdb_id, title=s.title, year=s.year,
                    season=covered[0].season, episode=covered[0].number,
                    last_episode=covered[-1].number, size=f.size, progress_bytes=f.size,
                    mtime=_mtime(path, now), path=path, torrent_id=None, file_index=None,
                    download_bps=0.0, eta_s=None, state="done", ext=path.suffix.lower(),
                ))
        return out

    def _all_imported(self, row: ShowDownload) -> bool:
        data = self._series_data.get(row.series_id)
        if data is None or not row.episode_ids:
            return False
        by_id = {e.id: e for e in data[0]}
        return all(eid in by_id and by_id[eid].has_file for eid in row.episode_ids)

    async def _delete_imported(self, rows: dict[str, ShowDownload],
                               groups: dict[str, list[QueueItem]],
                               torrents: dict[str, TorrentSummary], now: float) -> None:
        """The finished-torrent rule: once imported, remove the (paused) download copy."""
        for h, row in list(rows.items()):
            if row.status != "finished" or h in groups:
                continue
            t = torrents.get(h)
            if t is None:  # already gone from rqbit: nothing left to clean up
                self.store.delete_show_download(h)
                del rows[h]
                continue
            if (row.finished_at is None or now - row.finished_at < DELETE_AFTER_FINISH_SECONDS
                    or self.open_streams[t.id] > 0 or self.open_streams[row.torrent_id] > 0
                    or not self._all_imported(row)):
                continue
            try:
                await self.rqbit.delete(t.id)
            except RqbitError as e:
                if e.status != 404:
                    raise
            log.info("removed imported show torrent %s", t.id)
            self.store.delete_show_download(h)
            del rows[h]

    def _failed(self, rows: dict[str, ShowDownload], groups: dict[str, list[QueueItem]],
                series: dict[int, SonarrSeries], now: float) -> list[EpisodeEntry]:
        grabbed_by: dict[int, set[str]] = {}
        for h, items in groups.items():
            for i in items:
                grabbed_by.setdefault(i.episode_id, set()).add(h)
        out = []
        for h, row in list(rows.items()):
            if row.status != "failed":
                continue
            ended = row.finished_at if row.finished_at is not None else row.added_at
            regrabbed = any(grabbed_by.get(eid, set()) - {h} for eid in row.episode_ids)
            if regrabbed or now - ended >= FAILED_KEEP_SECONDS:
                self.store.delete_show_download(h)
                del rows[h]
                continue
            s = series.get(row.series_id)
            data = self._series_data.get(row.series_id)
            if s is None or data is None:
                continue
            by_id = {e.id: e for e in data[0]}
            for eid in row.episode_ids:
                e = by_id.get(eid)
                if e is None or e.has_file or e.season == 0:
                    continue
                out.append(EpisodeEntry(
                    series_id=s.id, show_tmdb_id=s.tmdb_id, title=s.title, year=s.year,
                    season=e.season, episode=e.number, last_episode=e.number, size=0,
                    progress_bytes=0, mtime=ended, path=None, torrent_id=None,
                    file_index=None, download_bps=0.0, eta_s=None, state="failed",
                ))
        out.sort(key=lambda e: (e.dirname, e.season, e.episode))
        return out


async def run_shows_poller(tracker: ShowsTracker, interval: float = 5.0) -> None:
    tracker.watch_started = time.time()
    while True:
        try:
            await tracker.refresh()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("shows refresh failed")
        await asyncio.sleep(interval)
