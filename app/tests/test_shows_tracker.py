import asyncio
import os
import time
from pathlib import Path

import httpx
import pytest

from cinema.rqbit import RqbitClient, TorrentFile
from cinema.shows import ShowsTracker, match_files, run_shows_poller
from cinema.sonarr import QueueItem, SonarrClient
from cinema.store import Store
from tests.fakes import FakeRqbit, FakeSonarr

MIB = 1024**2
HASH = "ab" * 20
SID = 5


def item(id, episode_id, season, episode, *, absolute=None, download_id=HASH):
    return QueueItem(id=id, download_id=download_id, series_id=SID, episode_id=episode_id,
                     season=season, episode=episode, absolute=absolute,
                     status="downloading", tracked_state="downloading")


# -- match_files ---------------------------------------------------------------

def test_match_files_single_episode():
    files = [TorrentFile(0, "Breaking.Bad.S01E04.1080p.WEB.x264/Breaking.Bad.S01E04.1080p.mkv",
                         900 * MIB)]
    e4 = item(1, 104, 1, 4)
    assert match_files(files, [e4], anime=False) == {0: [e4]}


def test_match_files_pack_with_extras_and_double():
    files = [
        TorrentFile(0, "Show.S01.1080p/Show.S01E01.1080p.mkv", 900 * MIB),
        TorrentFile(1, "Show.S01.1080p/Show.S01E02E03.1080p.mkv", 1800 * MIB),
        TorrentFile(2, "Show.S01.1080p/Sample/show.s01e01.sample.mkv", 20 * MIB),
        TorrentFile(3, "Show.S01.1080p/Show.S01.1080p.nfo", 1000),
        TorrentFile(4, "Show.S01.1080p/Featurette.mkv", 300 * MIB),
    ]
    e1, e2, e3 = item(1, 101, 1, 1), item(2, 102, 1, 2), item(3, 103, 1, 3)
    got = match_files(files, [e1, e2, e3], anime=False)
    assert got == {0: [e1], 1: [e2, e3]}


def test_match_files_duplicate_episode_keeps_largest_file():
    files = [TorrentFile(0, "Show.S01E01.Recap.mkv", 50 * MIB),
             TorrentFile(1, "Show.S01E01.1080p.mkv", 900 * MIB)]
    e1 = item(1, 101, 1, 1)
    assert match_files(files, [e1], anime=False) == {1: [e1]}


def test_match_files_anime_absolute():
    files = [TorrentFile(0, "[Group] Frieren - 31 (1080p) [ABCD1234].mkv", 700 * MIB),
             TorrentFile(1, "[Group] Frieren - 32 (1080p) [ABCD1234].mkv", 700 * MIB)]
    s2e3 = item(1, 203, 2, 3, absolute=31)
    s2e4 = item(2, 204, 2, 4, absolute=32)
    assert match_files(files, [s2e3, s2e4], anime=True) == {0: [s2e3], 1: [s2e4]}
    # Without the anime rule, "31" is read as episode 31 of season 2: no match.
    assert match_files(files, [s2e3, s2e4], anime=False) == {}


def test_match_files_season_less_name_uses_the_only_season():
    files = [TorrentFile(0, "Show - Episode 6.mkv", 500 * MIB)]
    e6 = item(1, 106, 3, 6)
    assert match_files(files, [e6], anime=False) == {0: [e6]}
    other = item(2, 206, 4, 1)
    assert match_files(files, [e6, other], anime=False) == {}


def test_match_files_unknown_episode_dropped():
    files = [TorrentFile(0, "Show.S01E05.mkv", 500 * MIB),
             TorrentFile(1, "Show.S01E01E02.mkv", 900 * MIB),
             TorrentFile(2, "Show.S02E01.mkv", 500 * MIB)]
    e1 = item(1, 101, 1, 1)
    assert match_files(files, [e1], anime=False) == {}


# -- tracker -------------------------------------------------------------------

def series_rec(id=SID, title="Breaking Bad", year=2008, tmdb=1396, series_type="standard"):
    return {"id": id, "tvdbId": 81189, "tmdbId": tmdb, "title": title, "year": year,
            "status": "continuing", "seriesType": series_type, "seasons": []}


def ep_rec(id, season, number, *, file_id=None, absolute=None):
    return {"id": id, "seriesId": SID, "seasonNumber": season, "episodeNumber": number,
            "absoluteEpisodeNumber": absolute, "title": f"Episode {number}",
            "monitored": True, "hasFile": file_id is not None, "episodeFileId": file_id or 0}


def queue_rec(id, episode_id, season, number, *, download_id=HASH):
    return {"id": id, "downloadId": download_id, "episodeId": episode_id, "seriesId": SID,
            "seasonNumber": season, "status": "downloading",
            "trackedDownloadState": "downloading",
            "episode": {"seasonNumber": season, "episodeNumber": number,
                        "absoluteEpisodeNumber": None}}


class Env:
    def __init__(self, tmp_path: Path):
        self.clock = [1000.0]
        self.sonarr = FakeSonarr()
        self.rqbit = FakeRqbit()
        self.store = Store(tmp_path / "cinema.db")
        self.sonarr.series[SID] = series_rec()
        self.tracker = ShowsTracker(
            self.store,
            SonarrClient("http://sonarr:8989", "k",
                         httpx.AsyncClient(transport=self.sonarr.transport)),
            RqbitClient("http://rqbit:3030", httpx.AsyncClient(transport=self.rqbit.transport)),
            now=lambda: self.clock[0],
        )

    def import_file(self, path: Path, episode_ids: list[int], file_id: int = 9,
                    size: int = 500 * MIB) -> None:
        self.sonarr.files.append({"id": file_id, "seriesId": SID, "seasonNumber": 1,
                                  "path": str(path), "size": size})
        for e in self.sonarr.episodes:
            if e["id"] in episode_ids:
                e["hasFile"], e["episodeFileId"] = True, file_id


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def where(entries, episode):
    return [e for e in entries if e.episode == episode]


@pytest.mark.anyio
async def test_snapshot_lists_only_ready_files(env):
    env.sonarr.episodes = [ep_rec(101, 1, 1), ep_rec(102, 1, 2)]
    env.sonarr.queue = [queue_rec(1, 101, 1, 1), queue_rec(2, 102, 1, 2)]
    tid = env.rqbit.add_torrent(
        HASH, [(["Breaking.Bad.S01", "Breaking.Bad.S01E01.1080p.mkv"], 500 * MIB),
               (["Breaking.Bad.S01", "Breaking.Bad.S01E02.1080p.mkv"], 500 * MIB)],
        file_progress=[99 * MIB, 100 * MIB])
    await env.tracker.refresh()
    t = env.tracker
    assert t.ok
    [listed] = t.listed()
    assert (listed.season, listed.episode, listed.last_episode) == (1, 2, 2)
    assert listed.state == "ready" and listed.progress_bytes == 100 * MIB
    assert (listed.torrent_id, listed.file_index, listed.path) == (tid, 1, None)
    assert (listed.dirname, listed.season_dir, listed.filename) == \
        ("Breaking Bad (2008)", "Season 01", "Breaking Bad - S01E02.mkv")
    assert (listed.title, listed.year, listed.show_tmdb_id, listed.series_id) == \
        ("Breaking Bad", 2008, 1396, SID)
    assert listed.size == 500 * MIB and listed.download_bps > 0 and listed.eta_s == 90
    [preparing] = where(t.entries, 1)
    assert preparing.state == "preparing" and preparing.progress_bytes == 99 * MIB
    [row] = env.store.show_downloads()
    assert (row.info_hash, row.torrent_id, row.series_id, row.episode_ids, row.status) == \
        (HASH, tid, SID, [101, 102], "downloading")
    assert row.added_at == 1000.0 and listed.mtime == 1000.0


@pytest.mark.anyio
async def test_snapshot_matches_uppercase_download_id(env):
    env.sonarr.episodes = [ep_rec(101, 1, 1)]
    env.sonarr.queue = [queue_rec(1, 101, 1, 1, download_id=HASH.upper())]
    tid = env.rqbit.add_torrent(HASH, [(["Breaking.Bad.S01E01.mkv"], 500 * MIB)],
                                file_progress=[200 * MIB])
    await env.tracker.refresh()
    [listed] = env.tracker.listed()
    assert listed.torrent_id == tid and listed.episode == 1
    # And a mixed-case hash on the rqbit side.
    env.rqbit.torrents[tid]["info_hash"] = "Ab" * 20
    await env.tracker.refresh()
    assert [e.torrent_id for e in env.tracker.listed()] == [tid]


@pytest.mark.anyio
async def test_snapshot_file_wins_over_queue_same_path(env, tmp_path):
    env.sonarr.episodes = [ep_rec(101, 1, 1)]
    env.sonarr.queue = [queue_rec(1, 101, 1, 1)]
    env.rqbit.add_torrent(HASH, [(["Breaking.Bad.S01E01.1080p.mkv"], 500 * MIB)],
                          file_progress=[200 * MIB])
    await env.tracker.refresh()
    [streamed] = env.tracker.listed()
    assert streamed.path is None and streamed.torrent_id is not None

    # The import moment: Sonarr has the file while the queue still lists the download.
    lib = tmp_path / "shows" / "Breaking Bad (2008)" / "Season 01"
    lib.mkdir(parents=True)
    on_disk = lib / "Breaking.Bad.S01E01.1080p.WEB.mkv"
    on_disk.write_bytes(b"x")
    os.utime(on_disk, (1234.0, 1234.0))
    env.import_file(on_disk, [101])
    env.clock[0] = 1005.0
    await env.tracker.refresh()
    [served] = env.tracker.listed()
    assert (served.dirname, served.season_dir, served.filename) == \
        (streamed.dirname, streamed.season_dir, streamed.filename)
    assert served.path == on_disk and served.torrent_id is None and served.file_index is None
    assert served.state == "done" and served.mtime == 1234.0
    assert served.size == served.progress_bytes == 500 * MIB


@pytest.mark.anyio
async def test_disk_files_listed_with_multi_episode_name(env, tmp_path):
    env.sonarr.episodes = [ep_rec(101, 1, 1, file_id=7), ep_rec(102, 1, 2, file_id=7),
                           ep_rec(1, 0, 1, file_id=8)]
    env.sonarr.files = [
        {"id": 7, "seriesId": SID, "seasonNumber": 1, "size": 10,
         "path": str(tmp_path / "missing" / "Breaking.Bad.S01E01E02.mp4")},
        {"id": 8, "seriesId": SID, "seasonNumber": 0, "size": 10,
         "path": str(tmp_path / "missing" / "special.mkv")},
    ]
    await env.tracker.refresh()
    [entry] = env.tracker.listed()  # the special (season 0) stays hidden
    assert entry.filename == "Breaking Bad - S01E01-E02.mp4"
    assert entry.mtime == 1000.0  # file not readable: falls back to now


@pytest.mark.anyio
async def test_finished_torrent_paused_then_deleted_after_import(env, tmp_path):
    env.sonarr.episodes = [ep_rec(101, 1, 1)]
    env.sonarr.queue = [queue_rec(1, 101, 1, 1)]
    tid = env.rqbit.add_torrent(HASH, [(["Breaking.Bad.S01E01.mkv"], 500 * MIB)],
                                file_progress=[500 * MIB], finished=True)
    await env.tracker.refresh()
    assert env.rqbit.torrents[tid]["paused"]
    [row] = env.store.show_downloads()
    assert (row.status, row.finished_at) == ("finished", 1000.0)
    [entry] = env.tracker.listed()  # finished, not imported yet: streamed from rqbit
    assert entry.state == "done" and entry.torrent_id == tid and entry.path is None

    # Sonarr imports it and drops it from the queue.
    env.sonarr.queue = []
    env.import_file(tmp_path / "Breaking.Bad.S01E01.mkv", [101])
    env.tracker.invalidate(SID)
    env.clock[0] = 1030.0  # too early
    await env.tracker.refresh()
    assert not env.rqbit.torrents[tid]["deleted"]
    assert env.store.show_downloads() != []
    [entry] = env.tracker.listed()
    assert entry.path is not None and entry.torrent_id is None

    env.clock[0] = 1070.0
    env.tracker.open_streams[tid] += 1  # someone is still watching from rqbit
    await env.tracker.refresh()
    assert not env.rqbit.torrents[tid]["deleted"]

    env.tracker.open_streams[tid] -= 1
    await env.tracker.refresh()
    assert env.rqbit.torrents[tid]["deleted"]
    assert env.store.show_downloads() == []
    assert len(env.tracker.listed()) == 1


@pytest.mark.anyio
async def test_finished_torrent_kept_while_an_episode_is_not_imported(env, tmp_path):
    env.sonarr.episodes = [ep_rec(101, 1, 1), ep_rec(102, 1, 2)]
    env.sonarr.queue = [queue_rec(1, 101, 1, 1), queue_rec(2, 102, 1, 2)]
    tid = env.rqbit.add_torrent(HASH, [(["S01E01.mkv"], 500 * MIB), (["S01E02.mkv"], 500 * MIB)],
                                file_progress=[500 * MIB, 500 * MIB], finished=True)
    await env.tracker.refresh()
    env.sonarr.queue = []
    env.import_file(tmp_path / "S01E01.mkv", [101])
    env.tracker.invalidate(SID)
    env.clock[0] = 2000.0
    await env.tracker.refresh()
    assert not env.rqbit.torrents[tid]["deleted"]
    assert [r.status for r in env.store.show_downloads()] == ["finished"]


@pytest.mark.anyio
async def test_stalled_torrent_blocklisted_and_failed(env):
    env.tracker.watch_started = 0.0
    env.sonarr.episodes = [ep_rec(101, 1, 1), ep_rec(102, 1, 2)]
    env.sonarr.queue = [queue_rec(1, 101, 1, 1), queue_rec(2, 102, 1, 2)]
    env.rqbit.add_torrent(HASH, [(["S01E01.mkv"], 500 * MIB), (["S01E02.mkv"], 500 * MIB)],
                          file_progress=[150 * MIB, 0], live_peers=0)
    await env.tracker.refresh()
    env.clock[0] = 1000.0 + 599
    await env.tracker.refresh()
    assert not [c for c in env.sonarr.calls if c[0] == "DELETE"]
    assert len(env.tracker.listed()) == 1

    env.clock[0] = 1000.0 + 601
    await env.tracker.refresh()
    [(method, path, params)] = [c for c in env.sonarr.calls if c[0] == "DELETE"]
    assert path == "/api/v3/queue/1" and params["blocklist"] == "true"
    [row] = env.store.show_downloads()
    assert row.status == "failed"
    assert env.tracker.listed() == []
    assert sorted((e.season, e.episode, e.state) for e in env.tracker.failed) == \
        [(1, 1, "failed"), (1, 2, "failed")]

    # Still failed on the next cycle (the queue no longer holds it).
    env.clock[0] += 5
    await env.tracker.refresh()
    assert len(env.tracker.failed) == 2


@pytest.mark.anyio
async def test_stall_clock_starts_when_watching_began(env):
    env.sonarr.episodes = [ep_rec(101, 1, 1)]
    env.sonarr.queue = [queue_rec(1, 101, 1, 1)]
    env.rqbit.add_torrent(HASH, [(["S01E01.mkv"], 500 * MIB)], file_progress=[0], live_peers=0)
    await env.tracker.refresh()
    env.tracker.watch_started = 1500.0  # e.g. the app was down until then
    env.clock[0] = 1700.0
    await env.tracker.refresh()
    assert env.tracker.failed == []
    env.clock[0] = 2101.0
    await env.tracker.refresh()
    assert len(env.tracker.failed) == 1


@pytest.mark.anyio
async def test_failed_cleared_when_regrabbed(env):
    env.sonarr.episodes = [ep_rec(101, 1, 1)]
    env.sonarr.queue = [queue_rec(1, 101, 1, 1)]
    env.rqbit.add_torrent(HASH, [(["S01E01.mkv"], 500 * MIB)], file_progress=[0], live_peers=0)
    await env.tracker.refresh()
    env.clock[0] = 2000.0
    await env.tracker.refresh()
    assert len(env.tracker.failed) == 1

    other = "cd" * 20
    env.sonarr.queue = [queue_rec(3, 101, 1, 1, download_id=other)]
    env.rqbit.add_torrent(other, [(["S01E01.mkv"], 500 * MIB)], file_progress=[0])
    env.clock[0] = 2005.0
    await env.tracker.refresh()
    assert env.tracker.failed == []
    assert [(r.info_hash, r.status) for r in env.store.show_downloads()] == \
        [(other, "downloading")]
    assert [e.state for e in env.tracker.entries] == ["preparing"]


@pytest.mark.anyio
async def test_failed_dropped_after_a_day(env):
    env.sonarr.episodes = [ep_rec(101, 1, 1)]
    env.sonarr.queue = [queue_rec(1, 101, 1, 1)]
    env.rqbit.add_torrent(HASH, [(["S01E01.mkv"], 500 * MIB)], file_progress=[0], live_peers=0)
    await env.tracker.refresh()
    env.clock[0] = 2000.0
    await env.tracker.refresh()
    env.clock[0] = 2000.0 + 86400 - 1
    await env.tracker.refresh()
    assert len(env.tracker.failed) == 1
    env.clock[0] = 2000.0 + 86400
    await env.tracker.refresh()
    assert env.tracker.failed == [] and env.store.show_downloads() == []


@pytest.mark.anyio
async def test_tracker_keeps_last_snapshot_on_error(env):
    env.sonarr.episodes = [ep_rec(101, 1, 1)]
    env.sonarr.queue = [queue_rec(1, 101, 1, 1)]
    env.rqbit.add_torrent(HASH, [(["S01E01.mkv"], 500 * MIB)], file_progress=[200 * MIB])
    await env.tracker.refresh()
    before = env.tracker.listed()
    assert env.tracker.ok and len(before) == 1

    env.sonarr.fail_status = 503
    await env.tracker.refresh()
    assert not env.tracker.ok and env.tracker.listed() == before

    env.sonarr.fail_status = None
    env.sonarr.series[SID] = {"id": SID, "title": "Breaking Bad", "seasons": "nope"}  # malformed
    await env.tracker.refresh()
    assert not env.tracker.ok and env.tracker.listed() == before

    env.sonarr.series[SID] = series_rec()
    env.tracker.rqbit = RqbitClient("http://rqbit:3030", httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="down"))))
    await env.tracker.refresh()
    assert not env.tracker.ok and env.tracker.listed() == before

    env.tracker.rqbit = RqbitClient("http://rqbit:3030",
                                    httpx.AsyncClient(transport=env.rqbit.transport))
    await env.tracker.refresh()
    assert env.tracker.ok and env.tracker.listed() == before


@pytest.mark.anyio
async def test_run_shows_poller_starts_the_stall_clock_and_refreshes(env):
    env.sonarr.episodes = [ep_rec(101, 1, 1)]
    env.sonarr.queue = [queue_rec(1, 101, 1, 1)]
    env.rqbit.add_torrent(HASH, [(["S01E01.mkv"], 500 * MIB)], file_progress=[200 * MIB])
    before = time.time()
    task = asyncio.create_task(run_shows_poller(env.tracker, interval=0.01))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if env.tracker.ok:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert env.tracker.watch_started >= before
    assert env.tracker.ok and len(env.tracker.listed()) == 1
