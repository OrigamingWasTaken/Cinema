from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from cinema.config import load_settings
from cinema.main import create_app


def test_defaults():
    s = load_settings({"TMDB_TOKEN": "t"})
    assert s.tmdb_language == "en-US"
    assert s.prowlarr_url == "http://prowlarr:9696"
    assert s.rqbit_url == "http://rqbit:3030"
    assert s.movies_dir == Path("/data/movies")
    assert s.legacy_dir == Path("/data/media/movies")
    assert s.db_path == Path("/config/cinema.db")
    assert s.dav_user is None


def test_empty_values_fall_back_to_defaults():
    keys = ["TMDB_LANGUAGE", "PROWLARR_URL", "RQBIT_URL", "MOVIES_DIR", "LEGACY_DIR",
            "DB_PATH", "PROWLARR_CONFIG_XML", "PROWLARR_API_KEY", "DAV_USER", "DAV_PASSWORD"]
    s = load_settings({"TMDB_TOKEN": "t", **{k: "" for k in keys}})
    assert s == load_settings({"TMDB_TOKEN": "t"})
    assert s.tmdb_language == "en-US"
    assert s.prowlarr_config_xml == Path("/prowlarr-config/config.xml")


def test_missing_tmdb_token_raises():
    with pytest.raises(RuntimeError, match="TMDB_TOKEN"):
        load_settings({})


def test_prowlarr_key_from_config_xml(tmp_path):
    xml = tmp_path / "config.xml"
    xml.write_text("<Config><ApiKey>abc123</ApiKey></Config>")
    s = load_settings({"TMDB_TOKEN": "t", "PROWLARR_CONFIG_XML": str(xml)})
    assert s.prowlarr_api_key == "abc123"


def test_prowlarr_key_env_wins(tmp_path):
    xml = tmp_path / "config.xml"
    xml.write_text("<Config><ApiKey>abc123</ApiKey></Config>")
    s = load_settings({"TMDB_TOKEN": "t", "PROWLARR_API_KEY": "env",
                       "PROWLARR_CONFIG_XML": str(xml)})
    assert s.prowlarr_api_key == "env"


def test_prowlarr_key_missing_is_empty(tmp_path):
    s = load_settings({"TMDB_TOKEN": "t",
                       "PROWLARR_CONFIG_XML": str(tmp_path / "nope.xml")})
    assert s.prowlarr_api_key == ""


@pytest.mark.anyio
async def test_app_picks_up_prowlarr_key_written_after_start(settings, tmp_path):
    from dataclasses import replace

    from cinema.prowlarr import ProwlarrError
    from cinema.releases import Release

    xml = tmp_path / "prowlarr" / "config.xml"
    app = create_app(replace(settings, prowlarr_config_xml=xml), start_poller=False)
    magnet = Release(guid="g", title="t", size=1, seeders=1, indexer="i",
                     download_url=None, magnet="magnet:?xt=urn:btih:aa", info_hash=None)
    with pytest.raises(ProwlarrError):
        await app.state.prowlarr.resolve(magnet)
    xml.parent.mkdir()
    xml.write_text("<Config><ApiKey>fresh</ApiKey></Config>")
    assert await app.state.prowlarr.resolve(magnet) == magnet.magnet


def test_healthz(settings):
    client = TestClient(create_app(settings, start_poller=False))
    assert client.get("/healthz").json() == {"ok": True}
