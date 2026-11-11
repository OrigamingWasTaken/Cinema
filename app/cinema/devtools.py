"""Debug CLI: python -m cinema.devtools add --tmdb-id N --title T --year Y (--magnet M | --torrent-url U)"""
import argparse
import asyncio

import httpx

from cinema.config import load_settings
from cinema.downloads import start_download
from cinema.rqbit import RqbitClient
from cinema.store import Store, library_name


async def _add(args: argparse.Namespace) -> None:
    settings = load_settings()
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
        if args.magnet:
            source = args.magnet
        else:
            resp = await http.get(args.torrent_url)
            resp.raise_for_status()
            source = resp.content
        movie = await start_download(
            Store(settings.db_path), RqbitClient(settings.rqbit_url, http),
            settings.movies_dir, tmdb_id=args.tmdb_id, title=args.title,
            year=args.year, poster_path=None, runtime_min=None,
            release_name=args.title, source=source)
    print(f"library name: {library_name(movie.title, movie.year)}")
    print(f"file path: {movie.file_path}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="cinema.devtools")
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="add a torrent directly, bypassing search")
    add.add_argument("--tmdb-id", type=int, required=True)
    add.add_argument("--title", required=True)
    add.add_argument("--year", type=int)
    src = add.add_mutually_exclusive_group(required=True)
    src.add_argument("--magnet")
    src.add_argument("--torrent-url")
    args = parser.parse_args(argv)
    asyncio.run(_add(args))


if __name__ == "__main__":
    main()
