import httpx
import pytest

from cinema.downloads import STALL_SECONDS, refresh, remove_movie, run_poller, status_view
from cinema.rqbit import RqbitClient, RqbitError, TorrentStats
from cinema.store import Movie, Store
from cinema.webdav import READY_BYTES

GB = 1024**3


def movie(tmdb_id=1, **kw) -> Movie:
    base = dict(tmdb_id=tmdb_id, title="Sintel", year=2010, poster_path=None,
                runtime_min=100, release_name="Sintel.1080p", info_hash="a" * 40,
                torrent_id=tmdb_id, file_index=0,
                file_path="/data/movies/Sintel (2010)/Sintel (2010).mkv",
                size=4 * GB, status="downloading", added_at=1000.0, last_progress_at=1000.0)
    base.update(kw)
    return Movie(**base)


def stats(**kw) -> TorrentStats:
    base = dict(state="live", progress_bytes=0, total_bytes=4 * GB, finished=False,
                error=None, download_bps=0.0, live_peers=1, eta_s=None)
    base.update(kw)
    return TorrentStats(**base)


class StubRqbit:
    def __init__(self, by_id=None, delete_error=None):
        self.by_id = by_id or {}
        self.delete_error = delete_error
        self.paused: list[int] = []
        self.deleted: list[int] = []

    async def stats(self, tid):
        s = self.by_id[tid]
        if isinstance(s, Exception):
            raise s
        return s

    async def pause(self, tid):
        self.paused.append(tid)

    async def delete(self, tid):
        self.deleted.append(tid)
        if self.delete_error:
            raise self.delete_error


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


@pytest.mark.anyio
async def test_refresh_updates_progress(store):
    store.upsert(movie())
    rq = StubRqbit({1: stats(progress_bytes=500, download_bps=2e6, eta_s=77)})
    await refresh(store, rq, now=1100.0)
    m = store.get(1)
    assert (m.progress_bytes, m.download_bps, m.eta_s) == (500, 2e6, 77)
    assert m.last_progress_at == 1100.0 and m.status == "downloading"
    # no growth: last_progress_at is kept
    await refresh(store, rq, now=1200.0)
    assert store.get(1).last_progress_at == 1100.0


@pytest.mark.anyio
async def test_refresh_finished_pauses_and_marks_done(store):
    store.upsert(movie())
    rq = StubRqbit({1: stats(progress_bytes=4 * GB, finished=True)})
    await refresh(store, rq, now=1100.0)
    assert rq.paused == [1]
    m = store.get(1)
    assert m.status == "done" and m.eta_s is None and m.download_bps == 0


@pytest.mark.anyio
async def test_refresh_marks_stalled_failed(store):
    store.upsert(movie())
    rq = StubRqbit({1: stats(live_peers=0)})
    await refresh(store, rq, now=1000.0 + STALL_SECONDS)
    assert store.get(1).status == "downloading"
    await refresh(store, rq, now=1000.0 + STALL_SECONDS + 1)
    assert store.get(1).status == "failed"


@pytest.mark.anyio
async def test_refresh_not_failed_if_peers_present(store):
    store.upsert(movie())
    rq = StubRqbit({1: stats(live_peers=3)})
    await refresh(store, rq, now=1000.0 + STALL_SECONDS + 1)
    assert store.get(1).status == "downloading"


@pytest.mark.anyio
async def test_refresh_skips_rqbit_error_and_non_downloading(store):
    store.upsert(movie(1))
    store.upsert(movie(2, status="done"))
    store.upsert(movie(3))
    rq = StubRqbit({1: RqbitError(500, "boom"), 3: stats(progress_bytes=9)})
    await refresh(store, rq, now=1100.0)
    assert store.get(1).progress_bytes == 0
    assert store.get(3).progress_bytes == 9


@pytest.mark.anyio
async def test_refresh_does_not_resurrect_movie_deleted_mid_flight(store):
    store.upsert(movie())

    class DeletingRqbit(StubRqbit):
        async def stats(self, tid):
            store.delete(tid)  # user removes the movie while stats is in flight
            return stats(progress_bytes=500)

    rq = DeletingRqbit()
    await refresh(store, rq, now=1100.0)
    assert store.get(1) is None and rq.paused == []


@pytest.mark.anyio
async def test_refresh_finished_but_deleted_mid_flight_is_not_paused_or_written(store):
    store.upsert(movie())

    class DeletingRqbit(StubRqbit):
        async def stats(self, tid):
            store.delete(tid)
            return stats(progress_bytes=4 * GB, finished=True)

    await refresh(store, DeletingRqbit(), now=1100.0)
    assert store.get(1) is None


@pytest.mark.anyio
async def test_run_poller_survives_unexpected_exception(store):
    store.upsert(movie())
    calls = []

    class Flaky(StubRqbit):
        async def stats(self, tid):
            calls.append(tid)
            if len(calls) == 1:
                raise ValueError("bad payload")
            return stats(progress_bytes=5)

    import asyncio
    task = asyncio.create_task(run_poller(store, Flaky(), interval=0.01))
    for _ in range(200):
        if store.get(1).progress_bytes == 5:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert store.get(1).progress_bytes == 5


def _library(tmp_path, tmdb_id=1):
    movies = tmp_path / "movies"
    folder = movies / "Sintel (2010)"
    folder.mkdir(parents=True)
    f = folder / "Sintel (2010).mkv"
    f.write_bytes(b"x")
    return movies, folder, f


@pytest.mark.anyio
async def test_remove_deletes_torrent_folder_and_row(store, tmp_path):
    movies, folder, f = _library(tmp_path)
    store.upsert(movie(file_path=str(f)))
    rq = StubRqbit()
    await remove_movie(store, rq, movies, 1)
    assert rq.deleted == [1] and not folder.exists() and movies.exists()
    assert store.get(1) is None


@pytest.mark.anyio
async def test_remove_ignores_404_and_skips_missing_torrent(store, tmp_path):
    movies, folder, f = _library(tmp_path)
    store.upsert(movie(file_path=str(f)))
    await remove_movie(store, StubRqbit(delete_error=RqbitError(404, "gone")), movies, 1)
    assert not folder.exists() and store.get(1) is None
    # legacy entry: negative torrent id -> no rqbit call
    movies, folder, f = _library(tmp_path)
    store.upsert(movie(2, torrent_id=-1, file_path=str(f)))
    rq = StubRqbit()
    await remove_movie(store, rq, movies, 2)
    assert rq.deleted == [] and not folder.exists() and store.get(2) is None


@pytest.mark.anyio
async def test_remove_propagates_other_rqbit_errors_and_keeps_row(store, tmp_path):
    movies, folder, f = _library(tmp_path)
    store.upsert(movie(file_path=str(f)))
    with pytest.raises(RqbitError):
        await remove_movie(store, StubRqbit(delete_error=RqbitError(500, "x")), movies, 1)
    assert folder.exists() and store.get(1) is not None


@pytest.mark.anyio
async def test_remove_refuses_path_outside_movies_dir(store, tmp_path):
    movies = tmp_path / "movies"
    movies.mkdir()
    outside = tmp_path / "other" / "Sintel (2010)"
    outside.mkdir(parents=True)
    (outside / "a.mkv").write_bytes(b"x")
    sneaky = movies / ".." / "other" / "Sintel (2010)" / "a.mkv"
    store.upsert(movie(1, file_path=str(sneaky)))
    # file directly in movies_dir: must never rmtree movies_dir itself
    (movies / "b.mkv").write_bytes(b"x")
    store.upsert(movie(2, file_path=str(movies / "b.mkv")))
    for tid in (1, 2):
        await remove_movie(store, StubRqbit(), movies, tid)
        assert store.get(tid) is None
    assert outside.exists() and (movies / "b.mkv").exists()


def test_status_view_states_and_may_buffer():
    prep = status_view(movie(progress_bytes=READY_BYTES - 1))
    assert prep["state"] == "preparing" and prep["percent"] == 2
    ready = status_view(movie(progress_bytes=GB, download_bps=5e5, eta_s=300))
    assert ready == {"state": "ready", "percent": 25, "eta_s": 300, "may_buffer": True}
    # 4 GB over 100 min = ~699 KB/s needed
    assert status_view(movie(progress_bytes=GB, download_bps=1e6))["may_buffer"] is False
    assert status_view(movie(progress_bytes=GB, download_bps=5e5, runtime_min=None))["may_buffer"] is True
    done = status_view(movie(status="done", progress_bytes=0, download_bps=0.0))
    assert done == {"state": "done", "percent": 100, "eta_s": None, "may_buffer": False}
    failed = status_view(movie(status="failed", progress_bytes=GB))
    assert failed["state"] == "failed" and failed["may_buffer"] is False
    assert status_view(movie(size=0))["percent"] == 0
    assert status_view(movie(progress_bytes=9 * GB))["percent"] == 100


@pytest.mark.anyio
@pytest.mark.parametrize("remaining", [None, {"human_readable": "1m"}, {"duration": {}}])
async def test_stats_eta_tolerates_missing_duration(remaining):
    body = {"state": "live", "progress_bytes": 1, "total_bytes": 2, "finished": False,
            "live": {"time_remaining": remaining, "snapshot": {"peer_stats": {"live": 1}}}}
    transport = httpx.MockTransport(lambda r: httpx.Response(200, json=body))
    client = RqbitClient("http://rqbit:3030", httpx.AsyncClient(transport=transport))
    assert (await client.stats(0)).eta_s is None
