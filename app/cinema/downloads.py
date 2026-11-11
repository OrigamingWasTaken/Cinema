import time
from pathlib import Path

from cinema.rqbit import RqbitClient, TorrentSource, pick_video_file
from cinema.store import Movie, Store, library_name


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
