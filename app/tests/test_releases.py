import json
from pathlib import Path

import pytest

from cinema.releases import Release, classify, language_ok, matches_movie
from cinema.tmdb import MovieInfo

FIXTURE = Path(__file__).parent / "fixtures" / "releases.json"


def load_fixture() -> list[dict]:
    return json.loads(FIXTURE.read_text())


def make_release(row: dict) -> Release:
    return Release(
        guid=row["title"],
        title=row["title"],
        size=int(row["size_gb"] * 1e9),
        seeders=row["seeders"],
        indexer="test",
        download_url=None,
        magnet=None,
        info_hash=None,
    )


def rel(title: str) -> Release:
    return make_release({"title": title, "size_gb": 1.0, "seeders": 1})


def movie(**kw) -> MovieInfo:
    base = dict(
        id=1, imdb_id=None, title="", original_title="", year=None, runtime=None,
        original_language="en", overview="", poster_path=None, backdrop_path=None,
    )
    return MovieInfo(**{**base, **kw})


@pytest.mark.parametrize("row", load_fixture(), ids=lambda r: r["title"])
def test_classify_fixture(row):
    c = classify(make_release(row))
    for key, value in row["expect"].items():
        assert getattr(c, key) == value, key


def test_group_and_channels():
    c = classify(rel("Dune.Part.Two.2024.1080p.BluRay.x264.AAC5.1-[YTS.MX]"))
    assert c.group is not None and c.group.upper().startswith("YTS")
    assert c.channels == "5.1"
    c = classify(rel("Dune.Part.Two.2024.1080p.BluRay.REMUX.AVC.TrueHD.7.1.Atmos-FGT"))
    assert (c.group, c.channels) == ("FGT", "7.1")
    assert classify(rel("Parasite.2019.KOREAN.1080p.BluRay.x264.DTS-FGT")).channels is None


def test_matches_movie_accents_and_original_title():
    m = movie(title="Anatomy of a Fall", original_title="Anatomie d'une chute", year=2023)
    assert matches_movie(classify(rel("Anatomie.d.une.chute.2023.FRENCH.1080p.BluRay.x264-UTT")), m)
    assert matches_movie(classify(rel("Anatomy.of.a.Fall.2023.1080p.BluRay.x264-JustWatch")), m)


def test_matches_movie_multiword_title():
    m = movie(title="Dune: Part Two", original_title="Dune: Part Two", year=2024)
    assert matches_movie(classify(rel("Dune.Part.Two.2024.1080p.BluRay.x264-SPARKS")), m)
    assert not matches_movie(classify(rel("Dune.2021.1080p.BluRay.x264-GRP")), m)


def test_matches_movie_rejects_wrong_year():
    m = movie(title="Dune: Part Two", original_title="Dune: Part Two", year=2024)
    assert not matches_movie(classify(rel("Dune.Part.Two.2021.1080p.BluRay.x264-GRP")), m)


def test_language_rules():
    french = classify(rel("Movie.2020.FRENCH.1080p.BluRay.x264-GRP"))
    multi = classify(rel("Movie.2020.MULTi.1080p.BluRay.x264-GRP"))
    plain = classify(rel("Movie.2020.1080p.BluRay.x264-GRP"))
    dubbed = classify(rel("Movie.2020.1080p.BluRay.x264.DUBBED-GRP"))
    korean = classify(rel("Parasite.2019.KOREAN.1080p.BluRay.x264.DTS-FGT"))
    vostfr = classify(rel("Parasite.2019.VOSTFR.1080p.WEB.H264-GRP"))
    assert not language_ok(french, "en") and language_ok(multi, "en")
    assert language_ok(french, "fr") and language_ok(plain, "fr")
    assert not language_ok(dubbed, "ko") and not language_ok(dubbed, "fr")
    assert language_ok(korean, "ko") and language_ok(vostfr, "ko")
