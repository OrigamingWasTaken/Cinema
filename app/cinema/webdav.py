"""Read-only WebDAV view of the library for Infuse.

Finished files (and legacy files) are served from disk. Files that are still
downloading are proxied to rqbit's stream endpoint, which blocks until the
requested pieces have arrived.
"""
import asyncio
import base64
import re
import secrets
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING
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
from cinema.store import Store, library_name

if TYPE_CHECKING:  # cinema.shows imports this module
    from cinema.shows import ShowsTracker

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
CLIENT_GONE = 499  # never seen by anyone: the client disconnected before rqbit answered

ET.register_namespace("D", DAV_NS)


@dataclass
class _File:
    dirname: str
    filename: str
    size: int
    mtime: float
    path: Path | None
    stream: tuple[int, int] | None  # (torrent id, file index): set only while downloading

    @property
    def content_type(self) -> str:
        return CONTENT_TYPES.get(Path(self.filename).suffix.lower(), "application/octet-stream")


def _nfc(name: str) -> str:
    # macOS/tvOS clients and copied legacy folders may send or hold decomposed accents.
    return unicodedata.normalize("NFC", name)


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


def _file_response(f: _File, *parents: str) -> ET.Element:
    return _response(_href(*parents, f.filename, collection=False),
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
        dirname = _nfc(library_name(m.title, m.year))
        if dirname in library:
            continue
        path = Path(m.file_path)
        library[dirname] = _File(dirname, dirname + path.suffix, m.size, m.added_at, path,
                                 (m.torrent_id, m.file_index) if m.status == "downloading"
                                 else None)
    try:
        legacy_dirs = sorted(p for p in legacy_dir.iterdir() if p.is_dir())
    except OSError:
        legacy_dirs = []
    for d in legacy_dirs:
        dirname = _nfc(d.name)
        if dirname in library:
            continue
        try:
            videos = [(p, p.stat()) for p in d.iterdir()
                      if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS]
        except OSError:
            continue
        if videos:
            p, st = max(videos, key=lambda v: v[1].st_size)
            library[dirname] = _File(dirname, _nfc(p.name), st.st_size, st.st_mtime, p, None)
    return library


# show dirname -> season dir -> filename -> file
_ShowsTree = dict[str, dict[str, dict[str, _File]]]


def _shows_tree(tracker: "ShowsTracker") -> _ShowsTree:
    """The virtual Shows layout. On a name clash the first entry (tracker order) wins."""
    tree: _ShowsTree = {}
    for e in tracker.listed():
        if e.path is not None:
            stream = None
        elif e.torrent_id is not None and e.file_index is not None:
            stream = (e.torrent_id, e.file_index)
        else:
            continue  # nothing to serve it from
        show, season, filename = _nfc(e.dirname), _nfc(e.season_dir), _nfc(e.filename)
        files = tree.setdefault(show, {}).setdefault(season, {})
        if filename not in files:
            files[filename] = _File(show, filename, e.size, e.mtime, e.path, stream)
    return tree


def _latest(node: dict) -> float:
    return max(v.mtime if isinstance(v, _File) else _latest(v) for v in node.values())


def _release(streams: Counter[int], torrent_id: int) -> None:
    if streams[torrent_id] <= 1:
        streams.pop(torrent_id, None)
    else:
        streams[torrent_id] -= 1


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


async def _until_disconnect(receive) -> None:
    while (await receive())["type"] != "http.disconnect":
        pass


async def _open_upstream(request: Request, http: httpx.AsyncClient, url: str,
                         headers: dict[str, str]) -> httpx.Response | None:
    """Send the rqbit GET, or give up (None) if the client disconnects first.

    rqbit holds back its response headers until the requested pieces have arrived,
    which for a far seek takes a while; Infuse drops that request when the user
    seeks again, and the abandoned one must not keep pulling pieces.
    """
    sending = asyncio.ensure_future(http.send(
        http.build_request("GET", url, headers=headers, timeout=PROXY_TIMEOUT), stream=True))
    gone = asyncio.ensure_future(_until_disconnect(request.receive))
    client_gone = True  # also when this handler itself is cancelled mid-wait
    try:
        await asyncio.wait({sending, gone}, return_when=asyncio.FIRST_COMPLETED)
        client_gone = gone.done()
    finally:
        gone.cancel()
        sending.cancel()  # no-op once the response headers are in
        (result,) = await asyncio.gather(sending, return_exceptions=True)
        await asyncio.gather(gone, return_exceptions=True)
        if client_gone and isinstance(result, httpx.Response):
            await result.aclose()
    if client_gone or isinstance(result, asyncio.CancelledError):
        return None
    if isinstance(result, httpx.Response):
        return result
    raise result


def _authorized(request: Request, auth: tuple[str, str]) -> bool:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        user, _, password = base64.b64decode(token).decode().partition(":")
    except ValueError:  # bad base64, non-ASCII header, undecodable bytes
        return False
    return (secrets.compare_digest(user.encode(), auth[0].encode())
            & secrets.compare_digest(password.encode(), auth[1].encode()))


def build_dav_router(store: Store, rqbit: RqbitClient, http: httpx.AsyncClient,
                     legacy_dir: Path, auth: tuple[str, str] | None,
                     tracker: "ShowsTracker | None" = None) -> APIRouter:
    """WebDAV share: Movies always, Shows only when a shows tracker is given."""
    router = APIRouter()

    def shows_propfind(segments: list[str], depth_one: bool) -> Response:
        node: dict | _File = _shows_tree(tracker)
        for s in segments[1:]:
            if not isinstance(node, dict) or s not in node:
                return Response(status_code=404)
            node = node[s]
        if isinstance(node, _File):
            return _multistatus([_file_response(node, *segments[:-1])])
        out = [_response(_href(*segments, collection=True), name=segments[-1], collection=True,
                         mtime=_latest(node) if len(segments) > 1 else None)]
        if depth_one:
            for name, child in node.items():
                if isinstance(child, _File):
                    out.append(_file_response(child, *segments))
                else:
                    out.append(_response(_href(*segments, name, collection=True), name=name,
                                         collection=True, mtime=_latest(child)))
        return _multistatus(out)

    async def propfind(request: Request, segments: list[str]) -> Response:
        depth_one = request.headers.get("depth", "infinity").strip() != "0"
        if segments and segments[0] == "Shows" and tracker is not None:
            return shows_propfind(segments, depth_one)
        library = await run_in_threadpool(_scan_library, store, legacy_dir)
        out: list[ET.Element] = []
        if not segments:
            out.append(_response(_href(collection=True), name="", collection=True))
            if depth_one:
                out.append(_response(_href("Movies", collection=True), name="Movies",
                                     collection=True))
                if tracker is not None:
                    out.append(_response(_href("Shows", collection=True), name="Shows",
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
                out.append(_file_response(f, "Movies", f.dirname))
        elif (len(segments) == 3 and segments[0] == "Movies"
              and segments[1] in library and library[segments[1]].filename == segments[2]):
            f = library[segments[1]]
            out.append(_file_response(f, "Movies", f.dirname))
        else:
            return Response(status_code=404)
        return _multistatus(out)

    async def serve_file(request: Request, f: _File,
                         streams: Counter[int] | None = None) -> Response:
        """Serve f from disk, or proxy it from rqbit while it downloads.

        streams, if given, counts the open rqbit streams per torrent id.
        """
        if f.stream is None:
            if f.path is None or not f.path.is_file():
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
        torrent_id, file_index = f.stream
        url = rqbit.stream_url(torrent_id, file_index)
        try:
            upstream = await _open_upstream(request, http, url, upstream_headers)
        except httpx.HTTPError:
            return Response(status_code=502)
        if upstream is None:
            return Response(status_code=CLIENT_GONE)  # nobody is listening any more
        stack = AsyncExitStack()
        if streams is not None:
            streams[torrent_id] += 1
            stack.callback(_release, streams, torrent_id)  # runs after upstream.aclose
        stack.push_async_callback(upstream.aclose)
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
        segments = [_nfc(s) for s in request.path_params.get("path", "").split("/") if s]
        if method == "PROPFIND":
            return await propfind(request, segments)
        if len(segments) == 3 and segments[0] == "Movies":
            library = await run_in_threadpool(_scan_library, store, legacy_dir)
            f = library.get(segments[1])
            if f is not None and f.filename == segments[2]:
                return await serve_file(request, f)
        if len(segments) == 4 and segments[0] == "Shows" and tracker is not None:
            f = _shows_tree(tracker).get(segments[1], {}).get(segments[2], {}).get(segments[3])
            if f is not None:
                return await serve_file(request, f, tracker.open_streams)
        return Response(status_code=404)

    router.add_route("/dav", handler, methods=METHODS)
    router.add_route("/dav/{path:path}", handler, methods=METHODS)
    return router
