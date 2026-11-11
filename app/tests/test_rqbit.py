import httpx
import pytest

from cinema.rqbit import RqbitClient, RqbitError, TorrentFile, pick_video_file
from tests.fakes import FakeRqbit

FILES = [(["Sintel", "Sintel.mkv"], 1000), (["Sintel", "Sample", "sample.mkv"], 10),
         (["Sintel", "info.nfo"], 5)]


def make(fake: FakeRqbit) -> RqbitClient:
    return RqbitClient("http://rqbit:3030", httpx.AsyncClient(transport=fake.transport))


@pytest.mark.anyio
async def test_add_sends_only_files_and_output_folder():
    fake = FakeRqbit(FILES)
    added = await make(fake).add("magnet:?xt=urn:btih:abc", only_file=2,
                                 output_folder="/data/movies/Sintel (2010)")
    q = fake.requests[-1]["params"]
    assert q["only_files"] == "2"
    assert q["output_folder"] == "/data/movies/Sintel (2010)"
    assert q["overwrite"] == "true"
    assert added.id == 0 and 0 in fake.torrents


@pytest.mark.anyio
async def test_add_accepts_torrent_bytes_and_rejects_null_id():
    fake = FakeRqbit(FILES)
    client = make(fake)
    await client.add(b"d8:announce0:e", only_file=0, output_folder="/x")
    assert fake.torrents[0]["body"] == b"d8:announce0:e"

    def handler(request):
        return httpx.Response(200, json={"id": None, "details": {"info_hash": "h", "files": []},
                                         "output_folder": "/x"})
    bad = RqbitClient("http://r", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(RqbitError):
        await bad.add("magnet:?x", only_file=0, output_folder="/x")


@pytest.mark.anyio
async def test_list_files_parses_components():
    fake = FakeRqbit(FILES)
    added = await make(fake).list_files("magnet:?xt=urn:btih:abc")
    assert fake.requests[-1]["params"]["list_only"] == "true"
    assert fake.torrents == {}
    assert added.id is None and added.info_hash == "a" * 40 and added.name == "Sintel"
    assert added.files[1] == TorrentFile(1, "Sintel/Sample/sample.mkv", 10)


@pytest.mark.anyio
async def test_stats_live_and_paused():
    fake = FakeRqbit(FILES)
    client = make(fake)
    await client.add("magnet:?x", only_file=0, output_folder="/x")
    fake.content[(0, 0)] = b"x" * 100
    fake.available[(0, 0)] = 40
    s = await client.stats(0)
    assert (s.progress_bytes, s.total_bytes, s.finished) == (40, 100, False)
    assert s.download_bps == 1048576 and s.live_peers == 1
    await client.pause(0)
    s = await client.stats(0)
    assert s.state == "paused" and s.download_bps == 0
    assert s.live_peers == 0 and s.eta_s is None
    await client.delete(0)
    assert fake.torrents[0]["deleted"]


@pytest.mark.anyio
async def test_non_2xx_raises():
    with pytest.raises(RqbitError) as e:
        await make(FakeRqbit(FILES)).stats(99)
    assert e.value.status == 404


def test_pick_video_file_skips_sample_and_nonvideo():
    files = [TorrentFile(0, "a/info.nfo", 5), TorrentFile(1, "a/Sample/x.mkv", 900),
             TorrentFile(2, "a/movie.MP4", 100), TorrentFile(3, "a/movie.mkv", 500),
             TorrentFile(4, "a/Movie.SAMPLE.avi", 800)]
    assert pick_video_file(files).index == 3
    assert pick_video_file([TorrentFile(0, "a/x.txt", 1)]) is None


def test_stream_url():
    c = RqbitClient("http://rqbit:3030/", httpx.AsyncClient())
    assert c.stream_url(3, 2) == "http://rqbit:3030/torrents/3/stream/2"
