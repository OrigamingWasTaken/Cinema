from pathlib import Path

import httpx
import pytest

from cinema.downloads import AlreadyInLibraryError, NoVideoFileError, start_download
from cinema.rqbit import RqbitClient
from cinema.store import Store
from tests.fakes import FakeRqbit

FILES = [(["Sintel", "Sintel.mkv"], 1000), (["Sintel", "Sample", "sample.mkv"], 10),
         (["Sintel", "info.nfo"], 5)]
MAGNET = "magnet:?xt=urn:btih:abc"


def make(fake: FakeRqbit, tmp_path: Path):
    rqbit = RqbitClient("http://rqbit:3030", httpx.AsyncClient(transport=fake.transport))
    return Store(tmp_path / "c.db"), rqbit


async def add(store, rqbit, tmp_path, **kw):
    return await start_download(
        store, rqbit, tmp_path / "movies", tmdb_id=45745, title="Sintel", year=2010,
        poster_path="/p.jpg", runtime_min=15, release_name="Sintel.2010.1080p",
        source=MAGNET, **kw)


@pytest.mark.anyio
async def test_start_download_picks_video_and_folder(tmp_path):
    fake = FakeRqbit(FILES, info_hash="b" * 40)
    store, rqbit = make(fake, tmp_path)
    movie = await add(store, rqbit, tmp_path, now=1234.5)
    folder = tmp_path / "movies" / "Sintel (2010)"
    adds = [r for r in fake.requests if "list_only" not in r["params"]]
    assert adds[0]["params"]["only_files"] == "0"
    assert adds[0]["params"]["output_folder"] == str(folder)
    assert movie.status == "downloading"
    assert movie.file_index == 0 and movie.size == 1000
    assert movie.file_path == str(folder / "Sintel" / "Sintel.mkv")
    assert movie.info_hash == "b" * 40 and movie.torrent_id == 0
    assert movie.release_name == "Sintel.2010.1080p"
    assert movie.added_at == 1234.5 and movie.last_progress_at == 1234.5
    assert store.get(45745) == movie


@pytest.mark.anyio
async def test_start_download_no_video_raises(tmp_path):
    fake = FakeRqbit([(["x", "readme.txt"], 5)])
    store, rqbit = make(fake, tmp_path)
    with pytest.raises(NoVideoFileError):
        await add(store, rqbit, tmp_path)
    assert store.get(45745) is None and not fake.torrents


@pytest.mark.anyio
async def test_start_download_twice_raises(tmp_path):
    fake = FakeRqbit(FILES)
    store, rqbit = make(fake, tmp_path)
    await add(store, rqbit, tmp_path)
    n = len(fake.requests)
    with pytest.raises(AlreadyInLibraryError):
        await add(store, rqbit, tmp_path)
    assert len(fake.requests) == n
