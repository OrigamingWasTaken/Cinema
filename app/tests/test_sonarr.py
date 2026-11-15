import httpx
import pytest

from cinema.sonarr import SonarrClient, SonarrError
from tests.fakes import FakeSonarr

BASE = "http://sonarr:8989"


def make(fake: FakeSonarr, key="k") -> SonarrClient:
    return SonarrClient(BASE, key, httpx.AsyncClient(transport=fake.transport))


def lookup_result(tvdb=81189):
    return {"title": "Breaking Bad", "year": 2008, "tvdbId": tvdb, "tmdbId": 1396,
            "status": "ended", "seriesType": "standard", "monitorNewItems": "all",
            "seasons": [{"seasonNumber": 0, "monitored": True},
                        {"seasonNumber": 1, "monitored": True,
                         "statistics": {"episodeCount": 7, "episodeFileCount": 3}}]}


def record(id, download_id, episode_id, **kw):
    return {"id": id, "downloadId": download_id, "episodeId": episode_id, "seriesId": 5,
            "seasonNumber": 1, "status": "downloading",
            "trackedDownloadState": "downloading",
            "episode": {"seasonNumber": 1, "episodeNumber": episode_id, "absoluteEpisodeNumber": 9},
            **kw}


@pytest.mark.anyio
async def test_queue_lowercases_download_id_and_flattens_episode():
    fake = FakeSonarr()
    fake.queue = [
        record(1, "ABCDEF", 2),
        {"id": 2, "downloadId": "X"},  # no episodeId: skipped
        {**record(3, "AB", 4), "downloadId": None},  # no downloadId: skipped
        {"id": 4, "downloadId": "CD", "episodeId": 7, "seriesId": 5, "seasonNumber": 3,
         "status": "queued", "trackedDownloadState": "downloading"},  # no nested episode
    ]
    items = await make(fake).queue()
    assert [i.id for i in items] == [1, 4]
    a, b = items
    assert (a.download_id, a.series_id, a.episode_id, a.season, a.episode, a.absolute) == \
        ("abcdef", 5, 2, 1, 2, 9)
    assert (a.status, a.tracked_state) == ("downloading", "downloading")
    assert b.season == 3
    req = fake.requests[0]
    assert dict(req.url.params) == {"page": "1", "pageSize": "1000", "includeEpisode": "true"}
    assert req.headers["x-api-key"] == "k"


@pytest.mark.anyio
async def test_add_series_unmonitored_without_search():
    fake = FakeSonarr()
    fake.lookup[81189] = lookup_result()
    client = make(fake)
    found = await client.lookup_tvdb(81189)
    assert found.id is None and found.tvdb_id == 81189 and found.tmdb_id == 1396
    assert [(s.number, s.episode_count, s.episode_file_count) for s in found.seasons] == \
        [(0, 0, 0), (1, 7, 3)]
    added = await client.add_series(found, quality_profile_id=1, root_folder="/tv",
                                    series_type="anime")
    method, path, body = fake.calls[0]
    assert (method, path) == ("POST", "/api/v3/series")
    assert body["monitored"] is True and body["monitorNewItems"] == "none"
    assert body["seasonFolder"] is True
    assert body["qualityProfileId"] == 1 and body["rootFolderPath"] == "/tv"
    assert body["seriesType"] == "anime"
    assert all(s["monitored"] is False for s in body["seasons"])
    assert body["addOptions"] == {"monitor": "none", "searchForMissingEpisodes": False,
                                  "searchForCutoffUnmetEpisodes": False}
    assert added.id is not None and added.series_type == "anime"


@pytest.mark.anyio
async def test_add_series_with_first_time_choices():
    fake = FakeSonarr()
    fake.lookup[81189] = lookup_result()
    client = make(fake)
    found = await client.lookup_tvdb(81189)
    await client.add_series(found, quality_profile_id=1, root_folder="/tv",
                            series_type="standard", monitor="skip",
                            monitored_seasons=frozenset({1}), search_missing=True,
                            monitor_new_items="all")
    body = fake.calls[0][2]
    assert body["monitorNewItems"] == "all"
    assert {s["seasonNumber"]: s["monitored"] for s in body["seasons"]} == {0: False, 1: True}
    assert body["addOptions"] == {"monitor": "skip", "searchForMissingEpisodes": True,
                                  "searchForCutoffUnmetEpisodes": False}


@pytest.mark.anyio
async def test_monitor_and_season_search_calls():
    fake = FakeSonarr()
    client = make(fake)
    await client.monitor_episodes([1, 2], True)
    await client.season_search(5, 2)
    assert fake.calls == [
        ("PUT", "/api/v3/episode/monitor", {"episodeIds": [1, 2], "monitored": True}),
        ("POST", "/api/v3/command",
         {"name": "SeasonSearch", "seriesId": 5, "seasonNumber": 2}),
    ]


@pytest.mark.anyio
async def test_delete_episode_files_bulk():
    fake = FakeSonarr()
    fake.files = [{"id": 9, "seriesId": 5, "seasonNumber": 1, "path": "/tv/a.mkv", "size": 3}]
    client = make(fake)
    files = await client.episode_files(5)
    assert (files[0].id, files[0].season, files[0].path, files[0].size) == (9, 1, "/tv/a.mkv", 3)
    await client.delete_episode_files([9])
    assert fake.calls == [("DELETE", "/api/v3/episodefile/bulk", {"episodeFileIds": [9]})]
    assert fake.files == []


@pytest.mark.anyio
async def test_remove_from_queue_blocklists():
    fake = FakeSonarr()
    fake.queue = [record(3, "AB", 1)]
    await make(fake).remove_from_queue(3, blocklist=True)
    assert fake.calls == [("DELETE", "/api/v3/queue/3",
                           {"removeFromClient": "true", "blocklist": "true",
                            "skipRedownload": "false"})]
    assert fake.queue == []


@pytest.mark.anyio
async def test_api_key_callable_read_per_request():
    fake = FakeSonarr(api_key="late")
    keys = iter(["", "late"])
    client = make(fake, key=lambda: next(keys))
    with pytest.raises(SonarrError):
        await client.all_series()
    assert fake.requests == []
    assert await client.all_series() == []
    assert fake.requests[0].headers["x-api-key"] == "late"


@pytest.mark.anyio
async def test_non_2xx_raises_sonarr_error_with_status():
    fake = FakeSonarr()
    fake.fail_status = 503
    with pytest.raises(SonarrError) as e:
        await make(fake).all_series()
    assert e.value.status == 503

    def down(request):
        raise httpx.ConnectError("no")
    client = SonarrClient(BASE, "k", httpx.AsyncClient(transport=httpx.MockTransport(down)))
    with pytest.raises(SonarrError) as e:
        await client.all_series()
    assert e.value.status is None

    ok = FakeSonarr()
    with pytest.raises(SonarrError) as e:
        await make(ok).quality_profile_id("Nope")
    assert e.value.status == 404
    assert await make(ok).quality_profile_id("Cinema") == 1


@pytest.mark.anyio
async def test_lookup_returns_none_when_empty():
    assert await make(FakeSonarr()).lookup_tvdb(1) is None


@pytest.mark.anyio
async def test_episodes_and_update_series():
    fake = FakeSonarr()
    fake.series[5] = {"id": 5, "tvdbId": 1, "title": "X", "seriesType": "standard",
                      "monitorNewItems": "none", "seasons": []}
    fake.episodes = [{"id": 1, "seriesId": 5, "seasonNumber": 1, "episodeNumber": 2,
                      "absoluteEpisodeNumber": 2, "title": "E", "airDateUtc": "2020-01-01T00:00:00Z",
                      "monitored": False, "hasFile": True, "episodeFileId": 9}]
    client = make(fake)
    ep = (await client.episodes(5))[0]
    assert (ep.id, ep.season, ep.number, ep.absolute, ep.has_file, ep.episode_file_id) == \
        (1, 1, 2, 2, True, 9)
    series = (await client.all_series())[0]
    updated = await client.update_series(series, monitorNewItems="all")
    assert updated.monitor_new_items == "all"
    assert fake.calls[0][:2] == ("PUT", "/api/v3/series/5")
    assert fake.calls[0][2]["title"] == "X"
