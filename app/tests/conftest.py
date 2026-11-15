import pytest

from cinema.config import Settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def settings(tmp_path):
    return Settings(
        tmdb_token="t",
        tmdb_language="en-US",
        prowlarr_url="http://prowlarr:9696",
        prowlarr_api_key="",
        rqbit_url="http://rqbit:3030",
        movies_dir=tmp_path / "movies",
        legacy_dir=tmp_path / "legacy",
        db_path=tmp_path / "cinema.db",
        sonarr_url="http://sonarr:8989",
        sonarr_api_key="",
        sonarr_config_xml=None,
        shows_dir=tmp_path / "shows",
        dav_user=None,
        dav_password=None,
    )
