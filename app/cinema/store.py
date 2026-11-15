import json
import re
import sqlite3
import threading
from dataclasses import astuple, dataclass, fields
from pathlib import Path


@dataclass
class Movie:
    tmdb_id: int
    title: str
    year: int | None
    poster_path: str | None
    runtime_min: int | None
    release_name: str
    info_hash: str
    torrent_id: int
    file_index: int
    file_path: str
    size: int
    status: str  # "downloading" | "done" | "failed"
    progress_bytes: int = 0
    download_bps: float = 0.0
    eta_s: int | None = None
    added_at: float = 0.0
    last_progress_at: float = 0.0


_COLUMNS = [f.name for f in fields(Movie)]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS movies (
    tmdb_id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    year INTEGER,
    poster_path TEXT,
    runtime_min INTEGER,
    release_name TEXT NOT NULL,
    info_hash TEXT NOT NULL,
    torrent_id INTEGER NOT NULL,
    file_index INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    status TEXT NOT NULL,
    progress_bytes INTEGER NOT NULL DEFAULT 0,
    download_bps REAL NOT NULL DEFAULT 0,
    eta_s INTEGER,
    added_at REAL NOT NULL DEFAULT 0,
    last_progress_at REAL NOT NULL DEFAULT 0
)
"""


_SHOW_DOWNLOADS_SCHEMA = """
CREATE TABLE IF NOT EXISTS show_downloads (
    info_hash TEXT PRIMARY KEY,
    torrent_id INTEGER NOT NULL,
    series_id INTEGER NOT NULL,
    episode_ids TEXT NOT NULL,
    status TEXT NOT NULL,
    progress_bytes INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL,
    last_progress_at REAL NOT NULL,
    finished_at REAL
)
"""

_SEASON_REQUESTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS season_requests (
    series_tmdb_id INTEGER NOT NULL,
    season INTEGER NOT NULL,
    requested_at REAL NOT NULL,
    PRIMARY KEY (series_tmdb_id, season)
)
"""


@dataclass
class ShowDownload:
    """One Sonarr-grabbed torrent, keyed by its (lower-case) info hash."""
    info_hash: str
    torrent_id: int
    series_id: int
    episode_ids: list[int]
    status: str  # "downloading" | "finished" | "failed"
    progress_bytes: int
    added_at: float
    last_progress_at: float
    finished_at: float | None  # when it finished, or failed


_SHOW_COLUMNS = [f.name for f in fields(ShowDownload)]


def _show_download(row: tuple) -> ShowDownload:
    d = ShowDownload(*row)
    d.episode_ids = json.loads(d.episode_ids)
    return d


def library_name(title: str, year: int | None) -> str:
    """Folder name "Title (Year)", safe for btrfs and WebDAV path segments."""
    name = title.replace(": ", " - ")
    name = re.sub(r'[/\\:*?"<>|]', "-", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    return f"{name} ({year})" if year is not None else name


class Store:
    def __init__(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        with self._lock, self._db:
            for schema in (_SCHEMA, _SHOW_DOWNLOADS_SCHEMA, _SEASON_REQUESTS_SCHEMA):
                self._db.execute(schema)

    def upsert(self, movie: Movie) -> None:
        marks = ", ".join("?" * len(_COLUMNS))
        with self._lock, self._db:
            self._db.execute(
                f"INSERT OR REPLACE INTO movies ({', '.join(_COLUMNS)}) VALUES ({marks})",
                astuple(movie),
            )

    def get(self, tmdb_id: int) -> Movie | None:
        with self._lock:
            row = self._db.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM movies WHERE tmdb_id = ?", (tmdb_id,)
            ).fetchone()
        return Movie(*row) if row else None

    def all(self) -> list[Movie]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM movies ORDER BY added_at DESC"
            ).fetchall()
        return [Movie(*r) for r in rows]

    def delete(self, tmdb_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM movies WHERE tmdb_id = ?", (tmdb_id,))

    def upsert_show_download(self, d: ShowDownload) -> None:
        values = [json.dumps(v) if k == "episode_ids" else v
                  for k, v in zip(_SHOW_COLUMNS, astuple(d))]
        marks = ", ".join("?" * len(_SHOW_COLUMNS))
        with self._lock, self._db:
            self._db.execute(
                f"INSERT OR REPLACE INTO show_downloads ({', '.join(_SHOW_COLUMNS)}) "
                f"VALUES ({marks})", values,
            )

    def show_downloads(self) -> list[ShowDownload]:
        with self._lock:
            rows = self._db.execute(
                f"SELECT {', '.join(_SHOW_COLUMNS)} FROM show_downloads "
                "ORDER BY added_at, info_hash"
            ).fetchall()
        return [_show_download(r) for r in rows]

    def delete_show_download(self, info_hash: str) -> None:
        with self._lock, self._db:
            self._db.execute("DELETE FROM show_downloads WHERE info_hash = ?", (info_hash,))

    def season_requested_at(self, series_tmdb_id: int, season: int) -> float | None:
        with self._lock:
            row = self._db.execute(
                "SELECT requested_at FROM season_requests "
                "WHERE series_tmdb_id = ? AND season = ?", (series_tmdb_id, season),
            ).fetchone()
        return row[0] if row else None

    def set_season_requested(self, series_tmdb_id: int, season: int, at: float) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO season_requests (series_tmdb_id, season, requested_at) "
                "VALUES (?, ?, ?)", (series_tmdb_id, season, at),
            )

    def clear_season_requested(self, series_tmdb_id: int, season: int) -> None:
        with self._lock, self._db:
            self._db.execute(
                "DELETE FROM season_requests WHERE series_tmdb_id = ? AND season = ?",
                (series_tmdb_id, season),
            )
