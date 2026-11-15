import asyncio
import logging
import shutil
import time
from dataclasses import replace
from pathlib import Path

from cinema.rqbit import RqbitClient, RqbitError, TorrentSource, pick_video_file
from cinema.store import Movie, Store, library_name
from cinema.webdav import READY_BYTES

log = logging.getLogger(__name__)

STALL_SECONDS = 600
DEFAULT_RUNTIME_MIN = 120


class NoVideoFileError(Exception):
    pass


class AlreadyInLibraryError(Exception):
    pass


async def start_download(store: Store, rqbit: RqbitClient, movies_dir: Path, *,
                         tmdb_id: int, title: str, year: int | None,
                         poster_path: str | None, runtime_min: int | None,
                         release_name: str, source: TorrentSource,
                         now: float | None = None) -> Movie:
    if store.get(tmdb_id) is not None:
        raise AlreadyInLibraryError(tmdb_id)
    listing = await rqbit.list_files(source)
    video = pick_video_file(listing.files)
    if video is None:
        raise NoVideoFileError(release_name)
    folder = movies_dir / library_name(title, year)
    added = await rqbit.add(source, only_file=video.index, output_folder=str(folder))
    stamp = time.time() if now is None else now
    movie = Movie(
        tmdb_id=tmdb_id, title=title, year=year, poster_path=poster_path,
        runtime_min=runtime_min, release_name=release_name,
        info_hash=added.info_hash, torrent_id=added.id, file_index=video.index,
        file_path=str(folder / video.path), size=video.length,
        status="downloading", added_at=stamp, last_progress_at=stamp,
    )
    store.upsert(movie)
    return movie


def _still_downloading(store: Store, m: Movie) -> Movie | None:
    """The current row for `m`, or None if it was removed or replaced since `m` was read.

    Re-read right before every write or rqbit call: the movie may have been removed
    (or removed and added again with a new torrent) while an rqbit call was in
    flight, and a stale full-row upsert would resurrect it (R7).
    """
    current = store.get(m.tmdb_id)
    if current is None or current.status != "downloading" or current.torrent_id != m.torrent_id:
        return None
    return current


async def _fail(store: Store, rqbit: RqbitClient, current: Movie) -> None:
    store.upsert(replace(current, status="failed", download_bps=0.0, eta_s=None))
    # Best effort: a failed torrent must not keep downloading or filling the disk.
    # The retry (R14) removes it for good.
    try:
        await rqbit.pause(current.torrent_id)
    except RqbitError as e:
        log.info("could not pause failed torrent %s: %s", current.torrent_id, e)


async def _refresh_one(store: Store, rqbit: RqbitClient, m: Movie, now: float,
                       watch_started: float | None) -> None:
    try:
        s = await rqbit.stats(m.torrent_id)
    except RqbitError as e:
        if e.status != 404:
            raise
        # rqbit does not know this torrent (any more): it will never finish.
        current = _still_downloading(store, m)
        if current is not None:
            log.warning("torrent %s of movie %s is unknown to rqbit", m.torrent_id, m.tmdb_id)
            await _fail(store, rqbit, current)
        return
    if s.finished and s.state != "paused":
        if _still_downloading(store, m) is None:
            return
        await rqbit.pause(m.torrent_id)
    current = _still_downloading(store, m)
    if current is None:
        return
    grew = s.progress_bytes > current.progress_bytes
    updated = replace(
        current, progress_bytes=s.progress_bytes, download_bps=s.download_bps,
        eta_s=s.eta_s, last_progress_at=now if grew else current.last_progress_at,
    )
    if s.finished:
        store.upsert(replace(updated, status="done", download_bps=0.0, eta_s=None))
    elif s.state == "error":
        log.warning("torrent %s for movie %s errored in rqbit", current.torrent_id, current.tmdb_id)
        await _fail(store, rqbit, updated)
    elif s.state == "live" and s.live_peers == 0 and _stalled(updated, now, watch_started):
        await _fail(store, rqbit, updated)
    else:
        store.upsert(updated)


def stalled_since(last_progress_at: float, now: float, watch_started: float | None) -> bool:
    # The stall clock never starts before this poller began watching: a NAS reboot or
    # app downtime is not time without peers.
    since = last_progress_at if watch_started is None else max(last_progress_at, watch_started)
    return now - since > STALL_SECONDS


def _stalled(m: Movie, now: float, watch_started: float | None) -> bool:
    return stalled_since(m.last_progress_at, now, watch_started)


async def refresh(store: Store, rqbit: RqbitClient, now: float,
                  watch_started: float | None = None) -> None:
    for m in store.all():
        if m.status != "downloading":
            continue
        try:
            await _refresh_one(store, rqbit, m, now, watch_started)
        except RqbitError as e:
            log.warning("rqbit error refreshing movie %s: %s", m.tmdb_id, e)


async def run_poller(store: Store, rqbit: RqbitClient, interval: float = 3.0) -> None:
    started = time.time()
    while True:
        try:
            await refresh(store, rqbit, time.time(), watch_started=started)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("download refresh failed")
        await asyncio.sleep(interval)


async def remove_movie(store: Store, rqbit: RqbitClient, movies_dir: Path,
                       tmdb_id: int) -> None:
    m = store.get(tmdb_id)
    if m is None:
        return
    try:
        await rqbit.delete(m.torrent_id)
    except RqbitError as e:
        if e.status != 404:
            raise
    # Exactly the output_folder start_download gave rqbit, whatever the torrent nests inside.
    root = Path(movies_dir).resolve()
    name = library_name(m.title, m.year)
    folder = (root / name).resolve() if name else root
    if folder != root and folder.is_relative_to(root):
        if folder.exists():
            try:
                shutil.rmtree(folder)
            except OSError as e:
                log.warning("could not remove %s: %s", folder, e)
    else:
        log.warning("not removing %r: not a folder inside movies dir %s", name, root)
    store.delete(tmdb_id)


def status_view(m: Movie) -> dict:
    if m.status == "done":
        return {"state": "done", "percent": 100, "eta_s": None, "may_buffer": False}
    percent = 0 if m.size <= 0 else max(0, min(100, int(100 * m.progress_bytes / m.size)))
    if m.status == "failed":
        return {"state": "failed", "percent": percent, "eta_s": None, "may_buffer": False}
    runtime_s = (m.runtime_min or DEFAULT_RUNTIME_MIN) * 60
    return {
        "state": "preparing" if m.progress_bytes < READY_BYTES else "ready",
        "percent": percent,
        "eta_s": m.eta_s,
        "may_buffer": m.download_bps < m.size / runtime_s,
    }
