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
            self._db.execute(_SCHEMA)

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
