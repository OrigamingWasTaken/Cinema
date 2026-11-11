from cinema.store import Movie, Store, library_name


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
