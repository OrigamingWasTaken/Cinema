import re
import unicodedata
from dataclasses import dataclass

from guessit import guessit

from cinema.tmdb import MovieInfo

_SPLIT = re.compile(r"[.\s_\-\[\]()]+")
_RESOLUTIONS = {720, 1080, 1440, 2160}
_CAM_SOURCES = {
    "camera", "hd camera", "telesync", "hd telesync", "telecine", "hd telecine",
    "screener", "digital screener", "workprint",
}
_CAM_TOKENS = {"hdcam", "cam", "ts", "tc", "scr", "r5"}
_FRENCH_TOKENS = {"french", "truefrench", "vff", "vfq", "vf", "vfi"}
_HDR_TOKENS = {"hdr", "hdr10", "hdr10+", "hdr10plus"}


@dataclass(frozen=True)
class Release:
    guid: str
    title: str
    size: int
    seeders: int
    indexer: str
    download_url: str | None
    magnet: str | None
    info_hash: str | None


@dataclass(frozen=True)
class Classified:
    release: Release
    resolution: int | None
    source: str
    atmos: bool
    channels: str | None
    lang: str
    group: str | None
    is_cam: bool
    is_3d: bool
    dv_only: bool
    parsed_title: str
    parsed_year: int | None


def _tokens(title: str) -> list[str]:
    return [t.lower() for t in _SPLIT.split(title) if t]


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return [str(value)]


def _resolution(guess, tokens: set[str]) -> int | None:
    size = guess.get("screen_size")
    if size:
        digits = re.match(r"\d+", str(size).lower().replace("4k", "2160"))
        if digits and int(digits.group()) in _RESOLUTIONS:
            return int(digits.group())
    if tokens & {"4k", "uhd"}:
        return 2160
    return None


def _source(guess, tokens: set[str], others: set[str]) -> str:
    if "remux" in tokens or "remux" in others:
        return "remux"
    source = str(guess.get("source") or "").lower()
    if "blu-ray" in source:
        return "bluray"
    if source == "web":
        if "webrip" in tokens or "rip" in others or "rip" in tokens:
            return "webrip"
        return "webdl"
    if "hdtv" in source:
        return "hdtv"
    return "other"


def _lang(tokens: set[str]) -> str:
    if "dubbed" in tokens:
        return "dubbed"
    if tokens & {"multi", "dual"}:
        return "multi"
    if tokens & _FRENCH_TOKENS:
        return "french_only"
    if tokens & {"vostfr", "subfrench"}:
        return "french_subs"
    return "original"


def _parsed_title(title: str, guess_title: str, year: int | None) -> str:
    # guessit treats "Part Two" style suffixes as extras, so cut at the year token.
    if year is not None:
        parts = _SPLIT.split(title)
        for i in range(len(parts) - 1, 0, -1):
            if parts[i] == str(year):
                return " ".join(p for p in parts[:i] if p)
    return str(guess_title or "")


def classify(r: Release) -> Classified:
    guess = guessit(r.title)
    toks = _tokens(r.title)
    tokens = set(toks)
    others = {o.lower() for o in _as_list(guess.get("other"))}
    year = guess.get("year")
    year = year if isinstance(year, int) else None
    camera = str(guess.get("source") or "").lower() in _CAM_SOURCES
    channels = guess.get("audio_channels")
    has_dv = bool(tokens & {"dv", "dovi"}) or "dolby vision" in others
    has_hdr = bool(tokens & _HDR_TOKENS) or any(o.startswith("hdr") for o in others)
    return Classified(
        release=r,
        resolution=_resolution(guess, tokens),
        source=_source(guess, tokens, others),
        atmos=("atmos" in tokens),
        channels=str(channels) if channels else None,
        lang=_lang(tokens),
        group=guess.get("release_group"),
        is_cam=camera or bool(tokens & _CAM_TOKENS),
        is_3d="3d" in others or "3d" in tokens,
        dv_only=has_dv and not has_hdr,
        parsed_title=_parsed_title(r.title, guess.get("title"), year),
        parsed_year=year,
    )


def _norm(title: str) -> str:
    text = unicodedata.normalize("NFKD", title)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", text)


def matches_movie(c: Classified, movie: MovieInfo) -> bool:
    if c.parsed_year is not None and movie.year is not None:
        if abs(c.parsed_year - movie.year) > 1:
            return False
    wanted = _norm(c.parsed_title)
    return wanted != "" and wanted in {_norm(movie.title), _norm(movie.original_title)}


def language_ok(c: Classified, original_language: str) -> bool:
    if c.lang == "dubbed":
        return False
    if original_language == "fr":
        return True
    return c.lang != "french_only"
