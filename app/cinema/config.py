import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from cinema.prowlarr import api_key_from_config_xml


@dataclass(frozen=True)
class Settings:
    tmdb_token: str
    tmdb_language: str
    prowlarr_url: str
    prowlarr_api_key: str
    rqbit_url: str
    movies_dir: Path
    legacy_dir: Path
    db_path: Path
    dav_user: str | None
    dav_password: str | None
    # Read again later while the key is still unknown (Prowlarr writes it on first start).
    prowlarr_config_xml: Path | None = None


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    def get(key: str, default: str) -> str:
        # compose passes unset variables as "", which must mean "use the default"
        return env.get(key) or default

    token = env.get("TMDB_TOKEN", "")
    if not token:
        raise RuntimeError("TMDB_TOKEN is required")
    config_xml = Path(get("PROWLARR_CONFIG_XML", "/prowlarr-config/config.xml"))
    api_key = env.get("PROWLARR_API_KEY") or api_key_from_config_xml(config_xml)
    return Settings(
        tmdb_token=token,
        tmdb_language=get("TMDB_LANGUAGE", "en-US"),
        prowlarr_url=get("PROWLARR_URL", "http://prowlarr:9696"),
        prowlarr_api_key=api_key,
        rqbit_url=get("RQBIT_URL", "http://rqbit:3030"),
        movies_dir=Path(get("MOVIES_DIR", "/data/movies")),
        legacy_dir=Path(get("LEGACY_DIR", "/data/media/movies")),
        db_path=Path(get("DB_PATH", "/config/cinema.db")),
        prowlarr_config_xml=config_xml,
        dav_user=env.get("DAV_USER") or None,
        dav_password=env.get("DAV_PASSWORD") or None,
    )
