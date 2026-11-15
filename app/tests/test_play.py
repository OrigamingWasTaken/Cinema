from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from cinema.hls import StreamSessions
from cinema.media import MediaError
from tests.test_hls import INFO, FakeRunner
from tests.test_webdav import CONTENT, Env
from tests.test_webdav_shows import entry

DEV = "device-0001"
MKV = replace(INFO, container="mkv")
MP4 = replace(INFO, container="mp4")


class FakeProber:
    def __init__(self, info=INFO):
        self.info = info
        self.error: Exception | None = None

    async def probe(self, path):
        if self.error:
            raise self.error
        return self.info


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
