import math
import re
from dataclasses import dataclass

from cinema.releases import Classified, language_ok, matches_movie
from cinema.tmdb import MovieInfo

_GB = 1e9
_DEFAULT_RUNTIME = 120
_FLOOR_GB_PER_MIN = {720: 0.5 * 0.045, 1080: 0.045, 1440: 0.045 * 1.3, 2160: 0.045 * 2}
_SWEET_SPOT_GB = {720: (4, 8), 1080: (8, 16), 1440: (10, 20), 2160: (15, 35)}
_SOURCE_SCORE = {"bluray": 30, "webdl": 20, "webrip": 10}
_BANNED_GROUPS = {"yts", "yify"}
_TIERS = [(1440, False), (1080, False), (2160, False), (1440, True), (1080, True), (2160, True)]
_GROUP_ORDER = [2160, 1440, 1080, 720]
_MIN_RECOMMENDED_SEEDERS = 3


@dataclass(frozen=True)
class Group:
    resolution: int
    standard: list[Classified]
    atmos: list[Classified]


def size_floor_bytes(resolution: int, runtime_min: int | None) -> int:
    runtime = runtime_min or _DEFAULT_RUNTIME
    return int(runtime * _FLOOR_GB_PER_MIN[resolution] * _GB)


def _size_score(c: Classified, movie: MovieInfo) -> float:
    scale = (movie.runtime or _DEFAULT_RUNTIME) / _DEFAULT_RUNTIME
    low, high = (v * scale * _GB for v in _SWEET_SPOT_GB[c.resolution])
    size = c.release.size
    if low <= size <= high:
        return 15.0
    if low * 0.5 < size < low:
        return 15.0 * (size - low * 0.5) / (low * 0.5)
    if high < size < high * 2:
        return 15.0 * (high * 2 - size) / high
    return 0.0


def score(c: Classified, movie: MovieInfo) -> float:
    total = float(_SOURCE_SCORE.get(c.source, 0))
    if c.lang == "original":
        total += 10
    if c.resolution in _SWEET_SPOT_GB:
        total += _size_score(c, movie)
    total += min(15.0, 5 * math.log10(c.release.seeders + 1))
    return total


def candidates(results: list[Classified], movie: MovieInfo) -> list[Classified]:
    return [
        c for c in results
        if matches_movie(c, movie)
        and language_ok(c, movie.original_language)
        and not c.is_cam
        and not c.is_3d
        and c.resolution is not None and c.resolution >= 720
        and c.release.seeders >= 1
    ]


def _ordered(items: list[Classified], movie: MovieInfo) -> list[Classified]:
    return sorted(items, key=lambda c: (-score(c, movie), -c.release.seeders, c.release.title))


def _is_banned(c: Classified) -> bool:
    parts = set()
    for text in (c.group or "", c.release.title):
        parts |= {p for p in re.split(r"[^a-z0-9]+", text.lower()) if p}
    return bool(parts & _BANNED_GROUPS)


def recommended(results: list[Classified], movie: MovieInfo) -> Classified | None:
    pool = [
        c for c in candidates(results, movie)
        if c.release.seeders >= _MIN_RECOMMENDED_SEEDERS
        and c.source != "remux"
        and not _is_banned(c)
        and not c.dv_only
        and c.resolution != 720
        and c.release.size >= size_floor_bytes(c.resolution, movie.runtime)
    ]
    for resolution, atmos in _TIERS:
        tier = [c for c in pool if c.resolution == resolution and c.atmos == atmos]
        if tier:
            return _ordered(tier, movie)[0]
    return None


def more_groups(results: list[Classified], movie: MovieInfo) -> list[Group]:
    pool = candidates(results, movie)
    groups = []
    for resolution in _GROUP_ORDER:
        at = [c for c in pool if c.resolution == resolution]
        standard = _ordered([c for c in at if not c.atmos], movie)
        atmos = _ordered([c for c in at if c.atmos], movie)
        if standard or atmos:
            groups.append(Group(resolution, standard, atmos))
    return groups


def speed_label(seeders: int) -> str:
    if seeders >= 50:
        return "fast"
    if seeders >= 10:
        return "ok"
    return "slow"
