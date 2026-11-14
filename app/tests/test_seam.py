"""One movie end to end: add -> finish -> Infuse plays it from disk -> remove."""
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from cinema.downloads import refresh, remove_movie, start_download
from cinema.main import create_app
from cinema.rqbit import RqbitClient
from cinema.store import Store
from tests.fakes import FakeRqbit

CONTENT = bytes(i % 251 for i in range(256 * 1024))
FILES = [(["Sintel.2010.1080p", "Sample", "sample.mkv"], 10),
         (["Sintel.2010.1080p", "Feature", "Sintel.2010.1080p.mkv"], len(CONTENT))]


@pytest.mark.anyio
async def test_download_finish_play_remove(settings, tmp_path):
    settings = replace(settings, movies_dir=tmp_path / "movies")
    fake = FakeRqbit(FILES)
    rqbit = RqbitClient("http://rqbit:3030", httpx.AsyncClient(transport=fake.transport))
    store = Store(tmp_path / "seam.db")

    m = await start_download(
        store, rqbit, settings.movies_dir, tmdb_id=45745, title="Sintel", year=2010,
        poster_path=None, runtime_min=15, release_name="Sintel.2010.1080p",
        source="magnet:?xt=urn:btih:abc", now=1000.0)
    folder = settings.movies_dir / "Sintel (2010)"
    assert m.file_index == 1
    assert Path(m.file_path) == folder / "Sintel.2010.1080p" / "Feature" / "Sintel.2010.1080p.mkv"

    # rqbit has every piece: the poller marks it done and pauses the torrent
    fake.content[(m.torrent_id, m.file_index)] = CONTENT
    await refresh(store, rqbit, now=1010.0, watch_started=1000.0)
    done = store.get(45745)
    assert done.status == "done" and done.progress_bytes == len(CONTENT)
    assert fake.torrents[m.torrent_id]["paused"] is True

    # the file rqbit wrote; Infuse gets it from disk, not through rqbit
    Path(m.file_path).parent.mkdir(parents=True)
    Path(m.file_path).write_bytes(CONTENT)
    app = create_app(settings, store=store, rqbit=rqbit, start_poller=False)
    before = len(fake.requests)
    r = TestClient(app).get("/dav/Movies/Sintel (2010)/Sintel (2010).mkv",
                            headers={"Range": "bytes=100-199"})
    assert r.status_code == 206 and r.content == CONTENT[100:200]
    assert len(fake.requests) == before

    await remove_movie(store, rqbit, settings.movies_dir, 45745)
    assert fake.torrents[m.torrent_id]["deleted"] is True
    assert not folder.exists() and settings.movies_dir.exists()
    assert store.get(45745) is None
