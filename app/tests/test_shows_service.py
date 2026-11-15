from datetime import datetime, timezone

import httpx
import pytest

from cinema.shows_service import (NotAiringError, NotAvailableError, ShowsService,
                                  season_state)
from cinema.sonarr import SonarrClient, SonarrEpisode, SonarrError
from cinema.store import Store
from cinema.tmdb import ShowCard, ShowInfo, TmdbError
from tests.fakes import FakeSonarr

NOW = datetime(2024, 1, 10, tzinfo=timezone.utc).timestamp()
PAST, FUTURE = "2024-01-05T02:00:00Z", "2024-02-05T02:00:00Z"
TVDB, TMDB = 81189, 1396


def ep(id, *, season=1, aired=True, has_file=False, date="default"):
    if date == "default":
        date = PAST if aired else FUTURE
    return SonarrEpisode(id=id, season=season, number=id, absolute=None, title=f"E{id}",
                         air_date_utc=date, monitored=False, has_file=has_file,
                         episode_file_id=id if has_file else None)


H = 3600


@pytest.mark.parametrize("episodes,monitored,queued,requested,state", [
    ([ep(1), ep(2)], True, {1}, None, "downloading"),
    ([ep(1, aired=False), ep(2, aired=False)], True, set(), None, "upcoming"),
    ([ep(1, date=None)], True, set(), None, "upcoming"),
    ([ep(1, date="garbage")], True, set(), None, "upcoming"),
    ([ep(1, has_file=True), ep(2, has_file=True), ep(3, aired=False)], True, set(), None, "done"),
    ([ep(1), ep(2)], True, set(), NOW - 23 * H, "looking"),
    ([ep(1), ep(2)], True, set(), NOW - 25 * H, "not_found"),
    ([ep(1), ep(2)], True, set(), None, "not_found"),
    ([ep(1, has_file=True), ep(2)], False, set(), NOW - H, "partial"),
    ([ep(1), ep(2)], False, set(), None, "none"),
])
def test_season_state_table(episodes, monitored, queued, requested, state):
    got = season_state(episodes, monitored=monitored, queued=queued,
                       requested_at=requested, now=NOW)
    assert got["state"] == state
    assert got["number"] == 1


def test_season_state_fresh_request_without_episodes_is_looking():
    got = season_state([], monitored=True, queued=set(), requested_at=NOW - H, now=NOW,
                       number=1)
    assert got["state"] == "looking"
    stale = season_state([], monitored=True, queued=set(), requested_at=NOW - 25 * H, now=NOW,
                         number=1)
    assert stale["state"] == "upcoming"


def test_season_state_counts():
    got = season_state([ep(1, has_file=True), ep(2), ep(3, aired=False)], monitored=True,
                       queued={2}, requested_at=None, now=NOW)
    assert got == {"number": 1, "state": "downloading", "on_tv": 1, "total": 3,
                   "aired": 2, "downloading": 1}


class FakeTmdb:
    def __init__(self):
        self.shows: dict[int, ShowInfo] = {}
        self.fail_tv: set[int] = set()
        self.fail_lists = False

    async def tv(self, tmdb_id):
        if tmdb_id in self.fail_tv or tmdb_id not in self.shows:
            raise TmdbError("nope", 404)
        return self.shows[tmdb_id]

    async def tv_trending(self):
        if self.fail_lists:
            raise TmdbError("down")
        return [ShowCard(7, "Trend", 2024, "/t.jpg")]

    async def tv_popular(self):
        if self.fail_lists:
            raise TmdbError("down")
        return [ShowCard(8, "Pop", 2024, "/p.jpg")]


class FakeTracker:
    def __init__(self):
        self.invalidated = []

    def invalidate(self, series_id):
        self.invalidated.append(series_id)


def info(id=TMDB, *, tvdb=TVDB, airing=False, anime=False, lang="en"):
    return ShowInfo(id=id, title=f"Show {id}", year=2008, end_year=None, overview="o",
                    original_language=lang, poster_path="/p", backdrop_path="/b",
                    airing=airing, anime=anime, tvdb_id=tvdb)


def lookup_result(tvdb=TVDB):
    return {"title": "Show", "year": 2008, "tvdbId": tvdb, "tmdbId": TMDB,
            "status": "continuing", "seriesType": "standard", "monitorNewItems": "all",
            "seasons": [{"seasonNumber": 0, "monitored": True},
                        {"seasonNumber": 1, "monitored": True},
                        {"seasonNumber": 2, "monitored": True}]}


def series_dict(id=5, *, tvdb=TVDB, tmdb=TMDB, new_items="none", files=0, monitored=False):
    return {"id": id, "title": "Show", "year": 2008, "tvdbId": tvdb, "tmdbId": tmdb,
            "status": "continuing", "seriesType": "standard", "monitorNewItems": new_items,
            "seasons": [{"seasonNumber": 0, "monitored": False},
                        {"seasonNumber": 1, "monitored": monitored,
                         "statistics": {"episodeFileCount": files}},
                        {"seasonNumber": 2, "monitored": False}]}


def ep_dict(id, season, *, aired=True, file_id=0, series_id=5):
    return {"id": id, "seriesId": series_id, "seasonNumber": season, "episodeNumber": id % 100,
            "title": f"E{id}", "airDateUtc": PAST if aired else FUTURE,
            "monitored": False, "hasFile": bool(file_id), "episodeFileId": file_id}


@pytest.fixture
def env(tmp_path):
    fake = FakeSonarr()
    tmdb = FakeTmdb()
    tmdb.shows[TMDB] = info()
    store = Store(tmp_path / "t.db")
    tracker = FakeTracker()
    sonarr = SonarrClient("http://sonarr:8989", "k", httpx.AsyncClient(transport=fake.transport))
    svc = ShowsService(store, sonarr, tmdb, tracker, shows_dir="/shows", now=lambda: NOW)
    svc.fake, svc.tmdb_fake, svc.tracker_fake = fake, tmdb, tracker
    return svc


def writes(fake):
    return [(m, p) for m, p, _ in fake.calls]


@pytest.mark.anyio
async def test_page_not_added_uses_lookup_seasons_without_episodes(env):
    env.fake.lookup[TVDB] = lookup_result()
    page = await env.page(TMDB)
    assert page["available"] and not page["following"]
    assert page["show"]["title"] == "Show 1396" and page["show"]["airing"] is False
    assert [s["number"] for s in page["seasons"]] == [1, 2]
    assert all(s["state"] == "none" and s["episodes"] is None for s in page["seasons"])


@pytest.mark.anyio
async def test_page_hides_season_zero(env):
    env.fake.series[5] = series_dict(monitored=True)
    env.fake.episodes = [ep_dict(100, 0), ep_dict(101, 1, file_id=9), ep_dict(102, 1),
                         ep_dict(103, 1, aired=False)]
    env.fake.queue = [{"id": 1, "downloadId": "AB", "episodeId": 102, "seriesId": 5,
                       "seasonNumber": 1, "episode": {"seasonNumber": 1, "episodeNumber": 2}}]
    page = await env.page(TMDB)
    assert [s["number"] for s in page["seasons"]] == [1, 2]
    s1 = page["seasons"][0]
    assert s1["state"] == "downloading"
    assert [e["state"] for e in s1["episodes"]] == ["on_tv", "downloading", "not_aired"]
    assert page["seasons"][1]["episodes"] == []
    assert not page["following"]


@pytest.mark.anyio
async def test_page_reports_season_monitored(env):
    # Not in Sonarr yet: nothing is monitored.
    env.fake.lookup[TVDB] = lookup_result()
    page = await env.page(TMDB)
    assert [s["monitored"] for s in page["seasons"]] == [False, False]
    # In Sonarr: each season's own flag, including one requested but not aired yet.
    env.fake.series[5] = series_dict(monitored=True)
    env.fake.episodes = [ep_dict(101, 1, aired=False), ep_dict(201, 2)]
    page = await env.page(TMDB)
    assert [(s["number"], s["state"], s["monitored"]) for s in page["seasons"]] == \
        [(1, "upcoming", True), (2, "none", False)]
    page = await env.remove_season(TMDB, 1)
    assert page["seasons"][0]["monitored"] is False


@pytest.mark.anyio
async def test_page_unavailable_without_tvdb(env):
    env.tmdb_fake.shows[TMDB] = info(tvdb=None)
    page = await env.page(TMDB)
    assert page["available"] is False and page["seasons"] == []
    env.tmdb_fake.shows[TMDB] = info()  # lookup finds nothing
    page = await env.page(TMDB)
    assert page["available"] is False and page["seasons"] == []


@pytest.mark.anyio
async def test_page_tmdb_error_propagates(env):
    with pytest.raises(TmdbError):
        await env.page(999)


def pending(fake, *episodes):
    fake.pending_episodes[TVDB] = [
        {"seasonNumber": n, "episodeNumber": i, "airDateUtc": PAST if aired else FUTURE}
        for n, i, aired in episodes]


def monitored(fake):
    return sorted((e["seasonNumber"], e["episodeNumber"]) for e in fake.episodes if e["monitored"])


@pytest.mark.anyio
async def test_get_season_adds_series_with_fr_profile_and_anime_type(env):
    env.tmdb_fake.shows[TMDB] = info(lang="fr", anime=True)
    env.fake.profiles = [{"id": 1, "name": "Cinema"}, {"id": 2, "name": "Cinema FR"}]
    env.fake.lookup[TVDB] = lookup_result()
    pending(env.fake, (1, 1, True), (1, 2, True), (2, 1, True))
    page = await env.get_season(TMDB, 1)
    # one add carrying the intent; no immediate monitor/search against a series with no episodes
    assert writes(env.fake) == [("POST", "/api/v3/series")]
    added = env.fake.calls[0][2]
    assert added["qualityProfileId"] == 2 and added["seriesType"] == "anime"
    assert added["rootFolderPath"] == "/shows"
    assert {s["seasonNumber"]: s["monitored"] for s in added["seasons"]} == \
        {0: False, 1: True, 2: False}
    assert added["addOptions"]["monitor"] == "skip"
    assert added["addOptions"]["searchForMissingEpisodes"] is True
    assert env.store.season_requested_at(TMDB, 1) == NOW
    assert env.tracker_fake.invalidated == [101]
    assert page["seasons"][0]["episodes"] == []  # Sonarr has not refreshed yet
    # Sonarr's refresh creates the episodes and applies the options
    env.fake.refresh(101, NOW)
    assert monitored(env.fake) == [(1, 1), (1, 2)]
    assert env.fake.calls[-1][2] == {"name": "MissingEpisodeSearch", "seriesId": 101}
    page = await env.page(TMDB)
    assert page["seasons"][0]["state"] == "looking"


@pytest.mark.anyio
async def test_get_season_existing_series_monitors_and_searches(env):
    env.fake.series[5] = series_dict()
    env.fake.episodes = [ep_dict(101, 1), ep_dict(102, 1), ep_dict(201, 2)]
    page = await env.get_season(TMDB, 1)
    assert writes(env.fake) == [("PUT", "/api/v3/series/5"), ("PUT", "/api/v3/episode/monitor"),
                                ("POST", "/api/v3/command")]
    assert env.fake.calls[1][2] == {"episodeIds": [101, 102], "monitored": True}
    assert page["seasons"][0]["state"] == "looking"
    assert env.tracker_fake.invalidated == [5]


@pytest.mark.anyio
async def test_get_season_unavailable(env):
    with pytest.raises(NotAvailableError):
        await env.get_season(TMDB, 1)  # lookup returns nothing
    env.tmdb_fake.shows[TMDB] = info(tvdb=None)
    with pytest.raises(NotAvailableError):
        await env.get_season(TMDB, 1)


@pytest.mark.anyio
async def test_follow_airing_also_gets_current_season(env):
    env.tmdb_fake.shows[TMDB] = info(airing=True)
    env.fake.series[5] = series_dict()
    env.fake.episodes = [ep_dict(101, 1, file_id=9), ep_dict(102, 1, aired=False),
                         ep_dict(201, 2, aired=False)]
    page = await env.follow(TMDB)
    assert writes(env.fake) == [("PUT", "/api/v3/series/5"), ("PUT", "/api/v3/series/5"),
                                ("PUT", "/api/v3/episode/monitor"), ("POST", "/api/v3/command")]
    assert env.fake.calls[0][2]["monitorNewItems"] == "all"
    assert env.fake.calls[3][2]["seasonNumber"] == 1
    assert page["following"] is True


@pytest.mark.anyio
async def test_follow_new_series_waits_for_refresh_then_gets_airing_season(env):
    env.tmdb_fake.shows[TMDB] = info(airing=True)
    env.fake.lookup[TVDB] = lookup_result()
    pending(env.fake, (1, 1, True), (1, 2, True), (2, 1, True), (2, 2, True), (2, 3, False))
    refreshed = []

    async def sleep(_):
        env.fake.refresh(101, NOW)
        refreshed.append(1)
    env._sleep = sleep
    page = await env.follow(TMDB)
    assert refreshed == [1]
    added = env.fake.calls[0][2]
    assert added["monitorNewItems"] == "all" and added["addOptions"]["monitor"] == "future"
    assert added["addOptions"]["searchForMissingEpisodes"] is False
    # season 2 is airing: its aired episodes and the future one end up monitored, season 1 not
    assert monitored(env.fake) == [(2, 1), (2, 2), (2, 3)]
    assert env.fake.calls[-1][2]["name"] == "SeasonSearch"
    assert env.fake.calls[-1][2]["seasonNumber"] == 2
    assert page["following"] is True


@pytest.mark.anyio
async def test_follow_new_series_between_seasons_monitors_only_future(env):
    env.tmdb_fake.shows[TMDB] = info(airing=True)
    env.fake.lookup[TVDB] = lookup_result()
    pending(env.fake, (1, 1, True), (1, 2, True), (2, 1, False), (2, 2, False))

    async def sleep(_):
        env.fake.refresh(101, NOW)
    env._sleep = sleep
    await env.follow(TMDB)
    assert monitored(env.fake) == [(2, 1), (2, 2)]
    assert not any(c[2].get("name") == "SeasonSearch" for c in env.fake.calls if c[0] == "POST"
                   and isinstance(c[2], dict) and "name" in c[2])


@pytest.mark.anyio
async def test_follow_new_series_gives_up_waiting(env):
    env.tmdb_fake.shows[TMDB] = info(airing=True)
    env.fake.lookup[TVDB] = lookup_result()
    pending(env.fake, (1, 1, True), (1, 2, False))
    waits = []

    async def sleep(s):
        waits.append(s)
    env._sleep = sleep
    page = await env.follow(TMDB)  # Sonarr never finishes: no episodes, no search
    assert len(waits) == 30
    assert writes(env.fake) == [("POST", "/api/v3/series")]
    assert page["following"] is True


@pytest.mark.anyio
async def test_follow_skips_airing_season_while_add_options_pending(env):
    env.tmdb_fake.shows[TMDB] = info(airing=True)
    env.fake.series[5] = {**series_dict(), "addOptions": {"monitor": "future"}}
    env.fake.episodes = [ep_dict(101, 1, file_id=9), ep_dict(102, 1, aired=False)]
    await env.follow(TMDB)
    assert writes(env.fake) == [("PUT", "/api/v3/series/5")]


@pytest.mark.anyio
async def test_follow_between_seasons_downloads_nothing(env):
    env.tmdb_fake.shows[TMDB] = info(airing=True)
    env.fake.series[5] = series_dict()
    env.fake.episodes = [ep_dict(101, 1, file_id=9), ep_dict(201, 2, aired=False),
                         ep_dict(202, 2, aired=False)]
    await env.follow(TMDB)
    assert writes(env.fake) == [("PUT", "/api/v3/series/5")]


@pytest.mark.anyio
async def test_follow_not_airing_raises(env):
    env.fake.series[5] = series_dict()
    with pytest.raises(NotAiringError):
        await env.follow(TMDB)
    assert env.fake.calls == []


@pytest.mark.anyio
async def test_unfollow_only_changes_monitor_new_items(env):
    env.fake.series[5] = series_dict(new_items="all")
    page = await env.unfollow(TMDB)
    assert writes(env.fake) == [("PUT", "/api/v3/series/5")]
    assert env.fake.calls[0][2]["monitorNewItems"] == "none"
    assert page["following"] is False


@pytest.mark.anyio
async def test_unfollow_and_remove_not_in_sonarr_are_noops(env):
    env.fake.lookup[TVDB] = lookup_result()
    assert (await env.unfollow(TMDB))["available"]
    assert (await env.remove_season(TMDB, 1))["available"]
    assert env.fake.calls == []


@pytest.mark.anyio
async def test_remove_season_deletes_files_unmonitors_and_dequeues(env):
    env.fake.series[5] = series_dict(monitored=True)
    env.fake.episodes = [ep_dict(101, 1, file_id=9), ep_dict(102, 1), ep_dict(201, 2, file_id=8)]
    env.fake.files = [{"id": 9, "seriesId": 5}, {"id": 8, "seriesId": 5}]
    env.fake.queue = [
        {"id": 1, "downloadId": "AB", "episodeId": 102, "seriesId": 5,
         "episode": {"seasonNumber": 1, "episodeNumber": 2}},
        {"id": 2, "downloadId": "CD", "episodeId": 201, "seriesId": 5,
         "episode": {"seasonNumber": 2, "episodeNumber": 1}}]
    env.store.set_season_requested(TMDB, 1, NOW)
    await env.remove_season(TMDB, 1)
    assert writes(env.fake) == [
        ("PUT", "/api/v3/series/5"), ("PUT", "/api/v3/episode/monitor"),
        ("DELETE", "/api/v3/episodefile/bulk"), ("DELETE", "/api/v3/queue/1")]
    seasons = {s["seasonNumber"]: s["monitored"] for s in env.fake.calls[0][2]["seasons"]}
    assert seasons[1] is False
    assert env.fake.calls[1][2] == {"episodeIds": [101, 102], "monitored": False}
    assert env.fake.calls[2][2] == {"episodeFileIds": [9]}
    assert env.fake.calls[3][2]["blocklist"] == "false"
    assert env.store.season_requested_at(TMDB, 1) is None
    assert env.tracker_fake.invalidated == [5]


@pytest.mark.anyio
async def test_remove_season_with_double_episode_file_and_season_pack(env):
    env.fake.series[5] = series_dict(monitored=True)
    env.fake.episodes = [ep_dict(101, 1, file_id=9), ep_dict(102, 1, file_id=9),  # S01E01E02
                         ep_dict(103, 1, file_id=10), ep_dict(104, 1), ep_dict(105, 1)]
    env.fake.files = [{"id": 9, "seriesId": 5}, {"id": 10, "seriesId": 5}]

    def rec(id, episode_id, download_id):
        return {"id": id, "downloadId": download_id, "episodeId": episode_id, "seriesId": 5,
                "episode": {"seasonNumber": 1, "episodeNumber": episode_id % 100}}
    # Sonarr v3 lists a season pack once per episode, all with the pack's downloadId.
    env.fake.queue = [rec(1, 104, "AB"), rec(2, 105, "AB"), rec(3, 104, "CD")]
    await env.remove_season(TMDB, 1)
    deletes = [(p, b) for m, p, b in env.fake.calls if m == "DELETE"]
    assert deletes[0] == ("/api/v3/episodefile/bulk", {"episodeFileIds": [9, 10]})
    assert [p for p, _ in deletes[1:]] == ["/api/v3/queue/1", "/api/v3/queue/3"]
    assert env.fake.files == [] and env.fake.queue == []


@pytest.mark.anyio
async def test_remove_season_tolerates_a_download_already_gone(env):
    env.fake.series[5] = series_dict(monitored=True)
    env.fake.episodes = [ep_dict(101, 1)]
    env.fake.queue = [{"id": 1, "downloadId": "AB", "episodeId": 101, "seriesId": 5,
                       "episode": {"seasonNumber": 1, "episodeNumber": 1}}]
    handle = env.fake._handle

    async def racing(request):  # Sonarr imports it between our read and the delete
        if request.method == "DELETE" and request.url.path == "/api/v3/queue/1":
            env.fake.queue = []
        return await handle(request)
    env.sonarr = SonarrClient("http://sonarr:8989", "k",
                              httpx.AsyncClient(transport=httpx.MockTransport(racing)))
    page = await env.remove_season(TMDB, 1)
    assert page["seasons"][0]["monitored"] is False


@pytest.mark.anyio
async def test_home_yours_and_library(env):
    for i in (1, 2, 3, 4, 5):
        env.tmdb_fake.shows[i] = info(i, tvdb=i)
    env.fake.series = {
        1: series_dict(1, tvdb=1, tmdb=1, new_items="all"),  # followed
        2: series_dict(2, tvdb=2, tmdb=2, monitored=True),  # monitored season
        3: series_dict(3, tvdb=3, tmdb=3, files=4),  # has files
        4: series_dict(4, tvdb=4, tmdb=4),  # nothing: not yours
        5: series_dict(5, tvdb=5, tmdb=None, new_items="all"),  # no tmdb id
    }
    home = await env.home()
    assert all(isinstance(c, ShowCard) for c in home["yours"])
    assert [c.id for c in home["yours"]] == [1, 2, 3]
    assert [c.id for c in home["library"]] == [3]
    assert [c.id for c in home["trending"]] == [7] and [c.id for c in home["popular"]] == [8]
    assert "sonarr_error" not in home and "catalog_error" not in home


@pytest.mark.anyio
async def test_home_skips_failed_card_and_survives_half_failures(env):
    env.tmdb_fake.shows[1] = info(1, tvdb=1)
    env.fake.series = {1: series_dict(1, tvdb=1, tmdb=1, new_items="all"),
                       2: series_dict(2, tvdb=2, tmdb=2, new_items="all")}
    env.tmdb_fake.fail_tv.add(2)
    env.tmdb_fake.fail_lists = True
    home = await env.home()
    assert [c.id for c in home["yours"]] == [1]
    assert home["trending"] == [] and home["popular"] == [] and home["catalog_error"] is True

    env.fake.fail_status = 500
    env.tmdb_fake.fail_lists = False
    home = await env.home()
    assert home["yours"] == [] and home["library"] == [] and home["sonarr_error"] is True
    assert home["trending"]


@pytest.mark.anyio
async def test_sonarr_error_propagates_from_actions(env):
    env.fake.fail_status = 500
    with pytest.raises(SonarrError):
        await env.get_season(TMDB, 1)
