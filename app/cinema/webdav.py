"""Read-only WebDAV view of the library for Infuse.

Finished files (and legacy files) are served from disk. Files that are still
downloading are proxied to rqbit's stream endpoint, which blocks until the
requested pieces have arrived.
"""
import base64
import binascii
import re
import secrets
import xml.etree.ElementTree as ET
from contextlib import AsyncExitStack
from dataclasses import dataclass
from email.utils import formatdate
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import FileResponse, Response, StreamingResponse

from cinema.rqbit import VIDEO_EXTENSIONS, RqbitClient
from cinema.store import Movie, Store, library_name

READY_BYTES = 100 * 1024**2
ALLOW = "OPTIONS, PROPFIND, GET, HEAD"
DAV_NS = "DAV:"
CONTENT_TYPES = {
    ".mkv": "video/x-matroska",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".avi": "video/x-msvideo",
}
# Every method a WebDAV client might send, so unsupported ones get 405, not a router 404/405 mix.
METHODS = ["GET", "HEAD", "OPTIONS", "PROPFIND", "PUT", "POST", "DELETE", "PATCH",
           "PROPPATCH", "MKCOL", "COPY", "MOVE", "LOCK", "UNLOCK"]
SUFFIX_RANGE = re.compile(r"bytes=-(\d+)")
PROXY_TIMEOUT = httpx.Timeout(10, read=None)

ET.register_namespace("D", DAV_NS)


@dataclass
class _File:
    dirname: str
    filename: str
    size: int
    mtime: float
    path: Path
    movie: Movie | None  # set only while the movie is still downloading

    @property
    def content_type(self) -> str:
        return CONTENT_TYPES.get(Path(self.filename).suffix.lower(), "application/octet-stream")


def _href(*segments: str, collection: bool) -> str:
    href = "/dav/" + "/".join(quote(s) for s in segments)
    return href + "/" if collection and segments else href


def _http_date(ts: float) -> str:
    return formatdate(ts, usegmt=True)


def _q(name: str) -> str:
    return f"{{{DAV_NS}}}{name}"


def _response(href: str, *, name: str, collection: bool, size: int | None = None,
              ctype: str | None = None, mtime: float | None = None) -> ET.Element:
    resp = ET.Element(_q("response"))
    ET.SubElement(resp, _q("href")).text = href
    propstat = ET.SubElement(resp, _q("propstat"))
    prop = ET.SubElement(propstat, _q("prop"))
    ET.SubElement(prop, _q("displayname")).text = name
    rtype = ET.SubElement(prop, _q("resourcetype"))
    if collection:
        ET.SubElement(rtype, _q("collection"))
    if size is not None:
        ET.SubElement(prop, _q("getcontentlength")).text = str(size)
    if ctype is not None:
        ET.SubElement(prop, _q("getcontenttype")).text = ctype
    if mtime is not None:
        ET.SubElement(prop, _q("getlastmodified")).text = _http_date(mtime)
    ET.SubElement(propstat, _q("status")).text = "HTTP/1.1 200 OK"
    return resp


def _multistatus(responses: list[ET.Element]) -> Response:
    root = ET.Element(_q("multistatus"))
    root.extend(responses)
    body = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return Response(body, status_code=207, media_type="application/xml; charset=utf-8")


def _file_response(f: _File) -> ET.Element:
    return _response(_href("Movies", f.dirname, f.filename, collection=False),
                     name=f.filename, collection=False, size=f.size,
                     ctype=f.content_type, mtime=f.mtime)


def _scan_library(store: Store, legacy_dir: Path) -> dict[str, _File]:
    """Map movie directory name -> its single video file. DB entries win over legacy."""
    library: dict[str, _File] = {}
    for m in store.all():
        if m.status == "failed":
            continue
        if m.status == "downloading" and m.progress_bytes < READY_BYTES:
            continue
        dirname = library_name(m.title, m.year)
        if dirname in library:
            continue
        path = Path(m.file_path)
        library[dirname] = _File(dirname, dirname + path.suffix, m.size, m.added_at, path,
                                 m if m.status == "downloading" else None)
    try:
        legacy_dirs = sorted(p for p in legacy_dir.iterdir() if p.is_dir())
    except OSError:
        legacy_dirs = []
    for d in legacy_dirs:
        if d.name in library:
            continue
        try:
            videos = [(p, p.stat()) for p in d.iterdir()
                      if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
        except OSError:
            continue
        if videos:
            p, st = max(videos, key=lambda v: v[1].st_size)
            library[d.name] = _File(d.name, p.name, st.st_size, st.st_mtime, p, None)
    return library


class _ProxyResponse(StreamingResponse):
    """Streaming response that always closes the upstream request when it ends.

    A client disconnect cancels this call, so the `finally` closes the rqbit
    stream whether or not the body generator ever started.
    """

    def __init__(self, upstream: httpx.Response, stack: AsyncExitStack, headers: dict[str, str]):
        super().__init__(self._body(upstream), status_code=upstream.status_code, headers=headers)
        self._stack = stack

    @staticmethod
    async def _body(upstream: httpx.Response):
        async for chunk in upstream.aiter_raw():
            yield chunk

    async def __call__(self, scope, receive, send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._stack.aclose()


def _authorized(request: Request, auth: tuple[str, str]) -> bool:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        user, _, password = base64.b64decode(token).decode().partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return False
    return (secrets.compare_digest(user.encode(), auth[0].encode())
            & secrets.compare_digest(password.encode(), auth[1].encode()))


def build_dav_router(store: Store, rqbit: RqbitClient, http: httpx.AsyncClient,
                     legacy_dir: Path, auth: tuple[str, str] | None) -> APIRouter:
    router = APIRouter()

    async def propfind(request: Request, segments: list[str]) -> Response:
        library = await run_in_threadpool(_scan_library, store, legacy_dir)
        depth_one = request.headers.get("depth", "infinity").strip() != "0"
        out: list[ET.Element] = []
        if not segments:
            out.append(_response(_href(collection=True), name="", collection=True))
            if depth_one:
                out.append(_response(_href("Movies", collection=True), name="Movies",
                                     collection=True))
        elif segments == ["Movies"]:
            out.append(_response(_href("Movies", collection=True), name="Movies",
                                 collection=True))
            if depth_one:
                for dirname, f in library.items():
                    out.append(_response(_href("Movies", dirname, collection=True),
                                         name=dirname, collection=True, mtime=f.mtime))
        elif len(segments) == 2 and segments[0] == "Movies" and segments[1] in library:
            f = library[segments[1]]
            out.append(_response(_href("Movies", f.dirname, collection=True), name=f.dirname,
                                 collection=True, mtime=f.mtime))
            if depth_one:
                out.append(_file_response(f))
        elif (len(segments) == 3 and segments[0] == "Movies"
              and segments[1] in library and library[segments[1]].filename == segments[2]):
            out.append(_file_response(library[segments[1]]))
        else:
            return Response(status_code=404)
        return _multistatus(out)

    async def serve_file(request: Request, f: _File) -> Response:
        if f.movie is None:
            if not f.path.is_file():
                return Response(status_code=404)
            return FileResponse(f.path, media_type=f.content_type)
        base = {"Accept-Ranges": "bytes", "Content-Type": f.content_type}
        if request.method == "HEAD":
            return Response(status_code=200, headers={**base, "Content-Length": str(f.size)})
        upstream_headers = {}
        if "range" in request.headers:
            rng = request.headers["range"]
            # rqbit ignores suffix ranges (answers 200 from byte 0), so make them absolute.
            suffix = SUFFIX_RANGE.fullmatch(rng.strip())
            if suffix:
                n = int(suffix.group(1))
                if n == 0:
                    return Response(status_code=416,
                                    headers={**base, "Content-Range": f"bytes */{f.size}"})
                rng = f"bytes={max(0, f.size - n)}-{f.size - 1}"
            upstream_headers["Range"] = rng
        url = rqbit.stream_url(f.movie.torrent_id, f.movie.file_index)
        stack = AsyncExitStack()
        try:
            upstream = await stack.enter_async_context(
                http.stream("GET", url, headers=upstream_headers, timeout=PROXY_TIMEOUT))
        except httpx.HTTPError:
            await stack.aclose()
            return Response(status_code=502)
        headers = dict(base)
        for name in ("Content-Range", "Content-Length", "Accept-Ranges"):
            if name in upstream.headers:
                headers[name] = upstream.headers[name]
        if upstream.status_code >= 400:  # e.g. 416: small body, relay it whole
            try:
                body = await upstream.aread()
            finally:
                await stack.aclose()
            return Response(body, status_code=upstream.status_code, headers=headers)
        return _ProxyResponse(upstream, stack, headers)

    async def handler(request: Request) -> Response:
        if auth is not None and not _authorized(request, auth):
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Cinema"'})
        method = request.method
        if method == "OPTIONS":
            return Response(status_code=200, headers={"DAV": "1", "Allow": ALLOW})
        if method not in ("PROPFIND", "GET", "HEAD"):
            return Response(status_code=405, headers={"Allow": ALLOW})
        segments = [s for s in request.path_params.get("path", "").split("/") if s]
        if method == "PROPFIND":
            return await propfind(request, segments)
        if len(segments) == 3 and segments[0] == "Movies":
            library = await run_in_threadpool(_scan_library, store, legacy_dir)
            f = library.get(segments[1])
            if f is not None and f.filename == segments[2]:
                return await serve_file(request, f)
        return Response(status_code=404)

    router.add_route("/dav", handler, methods=METHODS)
    router.add_route("/dav/{path:path}", handler, methods=METHODS)
    return router
