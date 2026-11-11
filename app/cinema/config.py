import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


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


def _key_from_config_xml(path: str) -> str:
    try:
        key = ET.parse(path).getroot().findtext("ApiKey")
    except (OSError, ET.ParseError):
        return ""
    return (key or "").strip()


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    token = env.get("TMDB_TOKEN", "")
    if not token:
        raise RuntimeError("TMDB_TOKEN is required")
    api_key = env.get("PROWLARR_API_KEY") or _key_from_config_xml(
        env.get("PROWLARR_CONFIG_XML", "/prowlarr-config/config.xml")
    )
    return Settings(
        tmdb_token=token,
        tmdb_language=env.get("TMDB_LANGUAGE", "en-US"),
        prowlarr_url=env.get("PROWLARR_URL", "http://prowlarr:9696"),
        prowlarr_api_key=api_key,
        rqbit_url=env.get("RQBIT_URL", "http://rqbit:3030"),
        movies_dir=Path(env.get("MOVIES_DIR", "/data/movies")),
        legacy_dir=Path(env.get("LEGACY_DIR", "/data/media/movies")),
        db_path=Path(env.get("DB_PATH", "/config/cinema.db")),
        dav_user=env.get("DAV_USER") or None,
        dav_password=env.get("DAV_PASSWORD") or None,
    )
