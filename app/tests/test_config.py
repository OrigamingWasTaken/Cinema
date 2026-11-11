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


def test_healthz(settings):
    client = TestClient(create_app(settings, start_poller=False))
    assert client.get("/healthz").json() == {"ok": True}
