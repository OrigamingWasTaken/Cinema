from cinema.ranking import (
    candidates, more_groups, recommended, size_floor_bytes, speed_label,
)
from cinema.releases import classify
from cinema.tmdb import MovieInfo
from tests.test_releases import load_fixture, make_release

DUNE = MovieInfo(
    id=1, imdb_id=None, title="Dune: Part Two", original_title="Dune: Part Two",
    year=2024, runtime=166, original_language="en", overview="",
    poster_path=None, backdrop_path=None,
)


def results(exclude=(), only=None):
    out = []
    for row in load_fixture():
        if any(x in row["title"] for x in exclude):
            continue
        if only is not None and not any(x in row["title"] for x in only):
            continue
        out.append(classify(make_release(row)))
    return out


def one(title, size_gb, seeders=100):
    return classify(make_release({"title": title, "size_gb": size_gb, "seeders": seeders}))


def short(c):
    return c.release.title.rsplit("-", 1)[-1].strip("[]")


def test_recommended_prefers_1440_std():
    best = recommended(results(), DUNE)
    assert best is not None and "1440p" in best.release.title


def test_recommended_falls_back_to_1080_bluray():
    best = recommended(results(exclude=["1440p"]), DUNE)
    assert best is not None and short(best) == "SPARKS"


def test_recommended_atmos_only_when_nothing_else():
    best = recommended(results(only=["FLUX", "FGT", "SWTYBLZ"]), DUNE)
    assert best is not None and short(best) == "FLUX"


def test_recommended_rejects_below_size_floor():
    only = [one("Dune.Part.Two.2024.1080p.BluRay.x264-GRP", 2.0)]
    assert recommended(only, DUNE) is None


def test_recommended_none_when_no_candidates():
    assert recommended([], DUNE) is None
    assert recommended(results(only=["HDCAM", "FRENCH"]), DUNE) is None


def test_recommended_excludes_yts_and_yify_variants():
    for group in ("[YTS.MX]", "YIFY", "[YTS.LT]"):
        c = one(f"Dune.Part.Two.2024.1080p.BluRay.x264-{group}", 12.0)
        assert recommended([c], DUNE) is None, group


def test_recommended_needs_three_seeders_and_no_remux():
    low = one("Dune.Part.Two.2024.1080p.BluRay.x264-GRP", 12.0, seeders=2)
    assert recommended([low], DUNE) is None
    assert low in candidates([low], DUNE)
    remux = one("Dune.Part.Two.2024.1080p.BluRay.REMUX.AVC-GRP", 30.0)
    assert recommended([remux], DUNE) is None


def test_more_groups_order_and_split():
    groups = more_groups(results(), DUNE)
    assert [g.resolution for g in groups] == [2160, 1440, 1080]
    by = {g.resolution: g for g in groups}
    std = by[1080].standard
    assert {short(c) for c in std} == {"SPARKS", "QTZ", "YTS.MX"}
    assert short(std[0]) == "SPARKS"
    assert {short(c) for c in by[1080].atmos} == {"FLUX", "FGT"}
    assert [short(c) for c in by[2160].standard] == ["XYZ"]
    assert [short(c) for c in by[2160].atmos] == ["SWTYBLZ"]
    everything = [c for g in groups for c in g.standard + g.atmos]
    assert not any(short(c) in {"NoGroup", "FW"} for c in everything)


def test_size_floor_bytes():
    assert size_floor_bytes(1080, 120) == int(120 * 0.045 * 1e9)
    assert size_floor_bytes(1080, None) == size_floor_bytes(1080, 120)
    assert size_floor_bytes(2160, 100) == int(100 * 0.045 * 2 * 1e9)


def test_speed_label():
    assert speed_label(50) == "fast"
    assert speed_label(49) == "ok"
    assert speed_label(10) == "ok"
    assert speed_label(9) == "slow"
    assert speed_label(0) == "slow"
