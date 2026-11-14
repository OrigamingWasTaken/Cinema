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


async def _refresh_one(store: Store, rqbit: RqbitClient, m: Movie, now: float) -> None:
    s = await rqbit.stats(m.torrent_id)
    if s.finished:
        # Only touch rqbit if the movie still exists (it may have been removed meanwhile).
        current = store.get(m.tmdb_id)
        if current is None or current.status != "downloading":
            return
        await rqbit.pause(m.torrent_id)
    # Re-read right before writing: the movie may have been removed while the stats
    # call was in flight, and a stale full-row upsert would resurrect it (R7).
    current = store.get(m.tmdb_id)
    if current is None or current.status != "downloading":
        return
    grew = s.progress_bytes > current.progress_bytes
    updated = replace(
        current, progress_bytes=s.progress_bytes, download_bps=s.download_bps,
        eta_s=s.eta_s, last_progress_at=now if grew else current.last_progress_at,
    )
    if s.finished:
        updated = replace(updated, status="done", download_bps=0.0, eta_s=None)
    elif now - updated.last_progress_at > STALL_SECONDS and s.live_peers == 0:
        updated = replace(updated, status="failed", download_bps=0.0, eta_s=None)
    store.upsert(updated)


async def refresh(store: Store, rqbit: RqbitClient, now: float) -> None:
    for m in store.all():
        if m.status != "downloading":
            continue
        try:
            await _refresh_one(store, rqbit, m, now)
        except RqbitError as e:
            log.warning("rqbit error refreshing movie %s: %s", m.tmdb_id, e)


async def run_poller(store: Store, rqbit: RqbitClient, interval: float = 3.0) -> None:
    while True:
        try:
            await refresh(store, rqbit, time.time())
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
    root = Path(movies_dir).resolve()
    parent = Path(m.file_path).parent.resolve()
    if parent.is_relative_to(root) and parent != root:
        # The library folder is the first path segment under movies_dir; a multi-file
        # torrent may nest the video deeper (Title (Year)/Sub.Dir/movie.mkv).
        folder = root / parent.relative_to(root).parts[0]
        if folder.exists():
            try:
                shutil.rmtree(folder)
            except OSError as e:
                log.warning("could not remove %s: %s", folder, e)
    else:
        log.warning("not removing %s: outside movies dir %s", parent, root)
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
