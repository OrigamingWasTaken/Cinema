import asyncio
import json
import logging
import time
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient

import cinema.main as main_mod
import cinema.play as play_mod
from cinema.hls import StreamSessions, Superseded
from cinema.media import MediaError, Prober
from tests.test_hls import INFO, FakeRunner
from tests.test_webdav import CONTENT, Env
from tests.test_webdav_shows import entry

DEV = "device-0001"
MKV = replace(INFO, container="mkv")
MP4 = replace(INFO, container="mp4", codec_tag="avc1", profile_known=True)


class FakeProber:
    def __init__(self, info=INFO):
        self.info = info
        self.error: Exception | None = None
        self.ready = True  # the keyframe index is built
        self.waits: list[float | None] = []

    async def probe(self, path):
        if self.error:
            raise self.error
        return replace(self.info, keyframes=())

    async def indexed(self, path, wait=None):
        self.waits.append(wait)
        if self.error:
            raise self.error
        return self.info if self.ready else None


class PlayEnv(Env):
    def __init__(self, tmp_path, settings, max_sessions=2):
        super().__init__(tmp_path, settings)
        self.prober = FakeProber()
        self.sessions = StreamSessions(tmp_root=tmp_path / "hls", runner=FakeRunner(),
                                       max_sessions=max_sessions)
        from cinema.main import create_app
        self.app = create_app(self.settings, store=self.store, rqbit=self.rqbit,
                              prober=self.prober, sessions=self.sessions, start_poller=False)
        self.tracker = self.app.state.tracker

    def show(self, name="ep.mkv", **kw):
        path = self.tmp / "shows" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(CONTENT)
        self.tracker.entries = [entry(path=path, state="done", torrent_id=None,
                                      file_index=None, **kw)]
        return path


@pytest.fixture
def env(tmp_path, settings):
    return PlayEnv(tmp_path, settings)


def test_play_info_movie_direct_mp4(env):
    m = env.add_done()
    mp4 = env.tmp / "movies" / "Sintel (2010)" / "Sintel.mp4"
    mp4.write_bytes(CONTENT)
    env.store.upsert(replace(m, file_path=str(mp4)))
    env.prober.info = MP4
    r = env.client().get(f"/api/play/movie/{m.tmdb_id}")
    assert r.status_code == 200
    assert r.json() == {
        "title": m.title, "subtitle": None, "duration": 300.0, "container": "mp4",
        "video_codec": "h264", "codec_string": "avc1.640028", "audio_codec": "ac3",
        "direct_url": f"/api/play/movie/{m.tmdb_id}/file",
        "hls_url": f"/api/play/movie/{m.tmdb_id}/index.m3u8"}


def test_play_info_mkv_has_no_direct_url(env):
    m = env.add_done()
    body = env.client().get(f"/api/play/movie/{m.tmdb_id}").json()
    assert body["direct_url"] is None
    assert body["hls_url"].endswith("/index.m3u8")


def test_play_info_episode_from_tracker(env):
    env.show(season=1, episode=1, last_episode=2)
    r = env.client().get("/api/play/episode/1396/1/2")
    assert r.status_code == 200
    body = r.json()
    assert (body["title"], body["subtitle"]) == ("Breaking Bad", "S01E02")
    assert body["hls_url"] == "/api/play/episode/1396/1/2/index.m3u8"


def test_play_unknown_or_unfinished_is_404(env):
    c = env.client()
    assert c.get("/api/play/movie/999").status_code == 404
    m = env.add_done()
    env.store.upsert(replace(m, status="downloading"))
    assert c.get(f"/api/play/movie/{m.tmdb_id}").json() == {"error": "not_found"}
    env.store.upsert(replace(m, status="done", file_path=str(env.tmp / "gone.mkv")))
    assert c.get(f"/api/play/movie/{m.tmdb_id}").status_code == 404
    path = env.show(season=1, episode=1, last_episode=2)
    assert c.get("/api/play/episode/1396/1/3").status_code == 404
    assert c.get("/api/play/episode/1396/2/1").status_code == 404
    env.tracker.entries = [replace(env.tracker.entries[0], state="ready")]
    assert c.get("/api/play/episode/1396/1/1").status_code == 404
    env.tracker.entries = [replace(env.tracker.entries[0], state="done", path=path.parent / "x")]
    assert c.get("/api/play/episode/1396/1/1").status_code == 404


def test_file_route_serves_ranges_mp4_only(env):
    m = env.add_done()
    c = env.client()
    assert c.get(f"/api/play/movie/{m.tmdb_id}/file").status_code == 404  # mkv
    mp4 = env.tmp / "movies" / "Sintel (2010)" / "Sintel.mp4"
    mp4.write_bytes(CONTENT)
    env.store.upsert(replace(m, file_path=str(mp4)))
    r = c.get(f"/api/play/movie/{m.tmdb_id}/file", headers={"Range": "bytes=2-5"})
    assert r.status_code == 206
    assert r.content == CONTENT[2:6]


def test_playlist_and_segments_through_fake_sessions(env):
    m = env.add_done()
    c = env.client()
    base = f"/api/play/movie/{m.tmdb_id}"
    r = c.get(f"{base}/index.m3u8", params={"d": DEV})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.apple.mpegurl")
    assert f'#EXT-X-MAP:URI="init.mp4?d={DEV}"' in r.text
    assert f"seg/0.m4s?d={DEV}" in r.text
    init = c.get(f"{base}/init.mp4", params={"d": DEV})
    assert (init.content, init.headers["content-type"]) == (b"init", "video/mp4")
    seg = c.get(f"{base}/seg/0.m4s", params={"d": DEV})
    assert (seg.content, seg.headers["content-type"]) == (b"seg0", "video/iso.segment")
    assert c.get(f"{base}/seg/9999.m4s", params={"d": DEV}).status_code == 404
    env.show()
    ep = c.get("/api/play/episode/1396/1/1/seg/0.m4s", params={"d": DEV})
    assert ep.content == b"seg0"


def test_vanished_segment_is_503_retry(env, monkeypatch):
    m = env.add_done()
    c = env.client()

    async def gone(*a, **k):
        return env.tmp / "nope.m4s"
    monkeypatch.setattr(env.sessions, "segment", gone)
    r = c.get(f"/api/play/movie/{m.tmdb_id}/seg/0.m4s", params={"d": DEV})
    assert r.status_code == 503
    assert r.headers["retry-after"] == "1"


def test_busy_is_429(tmp_path, settings):
    env = PlayEnv(tmp_path, settings, max_sessions=1)
    m = env.add_done()
    c = env.client()
    url = f"/api/play/movie/{m.tmdb_id}/index.m3u8"
    assert c.get(url, params={"d": DEV}).status_code == 200
    r = c.get(url, params={"d": "device-0002"})
    assert (r.status_code, r.json()) == (429, {"error": "busy"})


def test_bad_device_id_is_400(env):
    m = env.add_done()
    c = env.client()
    base = f"/api/play/movie/{m.tmdb_id}"
    for d in ("short", "bad id!!", "x" * 65):
        assert c.get(f"{base}/index.m3u8", params={"d": d}).status_code == 400
        assert c.get(f"{base}/seg/0.m4s", params={"d": d}).status_code == 400
    assert c.get(f"{base}/init.mp4").status_code == 400


def test_media_error_is_502_player(env):
    m = env.add_done()
    c = env.client()
    env.prober.error = MediaError("boom")
    r = c.get(f"/api/play/movie/{m.tmdb_id}")
    assert (r.status_code, r.json()) == (502, {"error": "upstream", "service": "player"})
    r = c.get(f"/api/play/movie/{m.tmdb_id}/index.m3u8", params={"d": DEV})
    assert r.status_code == 502
    env.prober.error = None

    async def full(*a, **k):
        raise OSError(28, "No space left on device")
    env.sessions.init = full
    r = c.get(f"/api/play/movie/{m.tmdb_id}/init.mp4", params={"d": DEV})
    assert (r.status_code, r.json()["service"]) == (502, "player")


def test_lifespan_clears_stale_runs_and_closes_sessions(tmp_path, settings):
    env = PlayEnv(tmp_path, settings)
    root = env.sessions.tmp_root
    stale = root / "stale"
    stale.mkdir(parents=True)
    (stale / "x.m4s").write_bytes(b"x")
    m = env.add_done()
    with TestClient(env.app) as c:
        assert not stale.exists() and root.is_dir()
        r = c.get(f"/api/play/movie/{m.tmdb_id}/index.m3u8", params={"d": DEV})
        assert r.status_code == 200
        assert len(list(root.iterdir())) == 1
    assert root.is_dir() and list(root.iterdir()) == []
    assert env.sessions.runner.procs and all(p.killed for p in env.sessions.runner.procs)


def test_episode_skips_a_matching_entry_without_a_file(env):
    path = env.show(season=1, episode=1, last_episode=1)
    missing = replace(env.tracker.entries[0], path=env.tmp / "gone.mkv")
    env.tracker.entries = [missing, replace(missing, path=path)]
    assert env.client().get("/api/play/episode/1396/1/1").status_code == 200


def test_lifespan_checks_ffmpeg_and_logs_a_too_old_one(tmp_path, settings, caplog):
    env = PlayEnv(tmp_path, settings)

    def too_old():
        env.sessions.runner.checks += 1
        raise MediaError("ffmpeg 5 is too old: browser playback needs FFmpeg 6 or newer")
    env.sessions.runner.check = too_old
    with TestClient(env.app) as c:
        assert c.get("/healthz").status_code == 200
    assert env.sessions.runner.checks == 1
    assert any(r.levelname == "ERROR" and "FFmpeg 6" in r.getMessage() for r in caplog.records)


def test_playlist_answers_preparing_until_the_index_is_built(env):
    m = env.add_done()
    c = env.client()
    base = f"/api/play/movie/{m.tmdb_id}"
    env.prober.ready = False
    for url in ("index.m3u8", "init.mp4", "seg/0.m4s"):
        r = c.get(f"{base}/{url}", params={"d": DEV})
        assert (r.status_code, r.json()) == (503, {"error": "preparing"}), url
        assert int(r.headers["retry-after"]) >= 1
    # no request waits longer than a reverse proxy would
    assert env.prober.waits and all(w is not None and w <= 20 for w in env.prober.waits)
    assert env.sessions.runner.starts == []  # no ffmpeg before the index exists
    env.prober.ready = True
    assert c.get(f"{base}/index.m3u8", params={"d": DEV}).status_code == 200


def test_info_never_waits_for_the_index_and_starts_it_for_hls_files(env):
    m = env.add_done()
    env.prober.ready = False
    c = env.client()
    r = c.get(f"/api/play/movie/{m.tmdb_id}")
    assert r.status_code == 200 and r.json()["direct_url"] is None
    assert env.prober.waits == [0]  # mkv: HLS ahead, so the index is started now
    mp4 = env.tmp / "movies" / "Sintel (2010)" / "Sintel.mp4"
    mp4.write_bytes(CONTENT)
    env.store.upsert(replace(m, file_path=str(mp4)))
    env.prober.info, env.prober.waits = MP4, []
    assert c.get(f"/api/play/movie/{m.tmdb_id}").json()["direct_url"].endswith("/file")
    assert env.prober.waits == []  # direct play needs no index


def test_hev1_or_unknown_profile_mp4_is_not_offered_direct(env):
    m = env.add_done()
    mp4 = env.tmp / "movies" / "Sintel (2010)" / "Sintel.mp4"
    mp4.write_bytes(CONTENT)
    env.store.upsert(replace(m, file_path=str(mp4)))
    c = env.client()
    env.prober.info = replace(MP4, video_codec="hevc", codec_string="hvc1.1.6.L120.90",
                              codec_tag="hev1")
    assert c.get(f"/api/play/movie/{m.tmdb_id}").json()["direct_url"] is None
    env.prober.info = replace(MP4, profile_known=False)
    assert c.get(f"/api/play/movie/{m.tmdb_id}").json()["direct_url"] is None
    env.prober.info = replace(MP4, video_codec="hevc", codec_string="hvc1.1.6.L120.90",
                              codec_tag="hvc1")
    assert c.get(f"/api/play/movie/{m.tmdb_id}").json()["direct_url"] is not None


def test_superseded_wait_logs_at_debug(env, caplog):
    m = env.add_done()
    c = env.client()

    async def superseded(*a, **k):
        raise Superseded("segment 3: the stream was restarted or closed")
    env.sessions.segment = superseded
    with caplog.at_level(logging.DEBUG, logger="cinema.play"):
        r = c.get(f"/api/play/movie/{m.tmdb_id}/seg/3.m4s", params={"d": DEV})
    assert r.status_code == 502
    play_logs = [r for r in caplog.records if r.name == "cinema.play"]
    assert play_logs and all(r.levelno == logging.DEBUG for r in play_logs)


def test_lifespan_closes_pollers_and_http_even_if_close_all_fails(tmp_path, settings,
                                                                monkeypatch):
    cancelled, closed = [], []

    async def poller(*a, **k):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.append(1)
            raise
    monkeypatch.setattr(main_mod, "run_poller", poller)
    monkeypatch.setattr(main_mod, "run_shows_poller", poller)
    real_aclose = httpx.AsyncClient.aclose

    async def aclose(self):
        closed.append(1)
        await real_aclose(self)
    monkeypatch.setattr(httpx.AsyncClient, "aclose", aclose)
    sessions = StreamSessions(tmp_root=tmp_path / "hls", runner=FakeRunner())

    async def boom():
        raise RuntimeError("close_all failed")
    sessions.close_all = boom
    app = main_mod.create_app(settings, prober=FakeProber(), sessions=sessions)
    with pytest.raises(RuntimeError, match="close_all failed"):
        with TestClient(app):
            pass
    assert (len(cancelled), closed) == (2, [1])


def test_preparing_then_ready_with_a_real_prober(tmp_path, settings, monkeypatch):
    monkeypatch.setattr(play_mod, "INDEX_WAIT_S", 0.2)
    scans = []

    async def ffprobe(args, timeout=None):
        if "csv=p=0" in args:  # the keyframe index of a long file
            scans.append(1)
            await asyncio.sleep(1.0)
            return "".join(f"{t}.0,K_\n" for t in range(0, 300, 2))
        return json.dumps({"format": {"duration": "300.0", "format_name": "matroska"},
                           "streams": [{"codec_type": "video", "codec_name": "h264",
                                        "profile": "High", "level": 40},
                                       {"codec_type": "audio", "codec_name": "ac3"}]})

    env = PlayEnv(tmp_path, settings)
    app = main_mod.create_app(env.settings, store=env.store, rqbit=env.rqbit,
                              prober=Prober(tmp_path / "cache", ffprobe), sessions=env.sessions,
                              start_poller=False)
    m = env.add_done()
    base = f"/api/play/movie/{m.tmdb_id}"
    with TestClient(app) as c:
        info = c.get(base)
        assert info.status_code == 200 and info.json()["duration"] == 300.0
        r = c.get(f"{base}/index.m3u8", params={"d": DEV})
        assert (r.status_code, r.json()) == (503, {"error": "preparing"})
        time.sleep(1.2)
        r = c.get(f"{base}/index.m3u8", params={"d": DEV})
        assert r.status_code == 200 and r.text.count("#EXTINF") == 50
    assert scans == [1]  # started by the info request, and only once
