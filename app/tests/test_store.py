import json
from dataclasses import replace

from cinema.store import Movie, ShowDownload, Store, library_name


def make_movie(tmdb_id: int = 1, added_at: float = 0.0, **kw) -> Movie:
    base = dict(tmdb_id=tmdb_id, title="Sintel", year=2010, poster_path="/p.jpg",
                runtime_min=15, release_name="Sintel.2010.1080p", info_hash="abc",
                torrent_id=3, file_index=2, file_path="/data/movies/Sintel (2010)/Sintel.mkv",
                size=1000, status="downloading", progress_bytes=10, download_bps=1.5,
                eta_s=60, added_at=added_at, last_progress_at=5.0)
    base.update(kw)
    return Movie(**base)


def test_upsert_get_roundtrip(tmp_path):
    store = Store(tmp_path / "state" / "cinema.db")
    movie = make_movie()
    store.upsert(movie)
    assert store.get(1) == movie
    store.upsert(make_movie(status="done", progress_bytes=1000, eta_s=None, poster_path=None))
    got = store.get(1)
    assert got.status == "done" and got.eta_s is None and got.poster_path is None
    assert store.get(99) is None


def test_all_orders_newest_first(tmp_path):
    store = Store(tmp_path / "cinema.db")
    store.upsert(make_movie(1, added_at=1.0))
    store.upsert(make_movie(2, added_at=3.0))
    store.upsert(make_movie(3, added_at=2.0))
    assert [m.tmdb_id for m in store.all()] == [2, 3, 1]


def test_delete(tmp_path):
    store = Store(tmp_path / "cinema.db")
    store.upsert(make_movie())
    store.delete(1)
    store.delete(1)
    assert store.get(1) is None and store.all() == []


def test_library_name_sanitizes():
    assert library_name("Mission: Impossible", 1996) == "Mission - Impossible (1996)"
    assert library_name("AC/DC: Let There Be Rock", 1980) == "AC-DC - Let There Be Rock (1980)"
    assert library_name("Amélie", 2001) == "Amélie (2001)"
    assert library_name("Untitled...", None) == "Untitled"


def test_store_show_download_roundtrip_and_episode_ids_json(tmp_path):
    store = Store(tmp_path / "cinema.db")
    d = ShowDownload(info_hash="ab" * 20, torrent_id=4, series_id=5, episode_ids=[11, 12],
                     status="downloading", progress_bytes=10, added_at=2.0,
                     last_progress_at=3.0, finished_at=None)
    store.upsert_show_download(d)
    store.upsert_show_download(ShowDownload("cd" * 20, 7, 6, [], "failed", 0, 1.0, 1.0, 9.0))
    assert store.show_downloads() == [
        ShowDownload("cd" * 20, 7, 6, [], "failed", 0, 1.0, 1.0, 9.0), d]
    raw = store._db.execute("SELECT episode_ids FROM show_downloads WHERE torrent_id = 4")
    assert json.loads(raw.fetchone()[0]) == [11, 12]
    done = replace(d, status="finished", finished_at=8.5)
    store.upsert_show_download(done)
    assert [x for x in store.show_downloads() if x.torrent_id == 4] == [done]
    store.delete_show_download("ab" * 20)
    store.delete_show_download("ab" * 20)
    assert [x.info_hash for x in store.show_downloads()] == ["cd" * 20]


def test_store_season_request_set_get_clear(tmp_path):
    path = tmp_path / "cinema.db"
    store = Store(path)
    assert store.season_requested_at(1396, 2) is None
    store.set_season_requested(1396, 2, 100.0)
    store.set_season_requested(1396, 3, 50.0)
    store.set_season_requested(1396, 2, 200.0)
    assert store.season_requested_at(1396, 2) == 200.0
    assert Store(path).season_requested_at(1396, 3) == 50.0  # persisted, schema reused
    store.clear_season_requested(1396, 2)
    store.clear_season_requested(1396, 2)
    assert store.season_requested_at(1396, 2) is None
    assert store.season_requested_at(1396, 3) == 50.0
