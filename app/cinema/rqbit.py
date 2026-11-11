from dataclasses import dataclass, field
from pathlib import PurePosixPath

import httpx

TorrentSource = str | bytes
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi"}
MIB = 1048576


class RqbitError(Exception):
    def __init__(self, status: int, text: str):
        super().__init__(f"rqbit error {status}: {text}")
        self.status = status
        self.text = text


@dataclass
class TorrentFile:
    index: int
    path: str
    length: int


@dataclass
class AddedTorrent:
    id: int | None
    info_hash: str
    name: str | None
    output_folder: str
    files: list[TorrentFile] = field(default_factory=list)


@dataclass
class TorrentStats:
    state: str
    progress_bytes: int
    total_bytes: int
    finished: bool
    error: str | None
    download_bps: float
    live_peers: int
    eta_s: int | None


def pick_video_file(files: list[TorrentFile]) -> TorrentFile | None:
    candidates = [
        f for f in files
        if PurePosixPath(f.path).suffix.lower() in VIDEO_EXTENSIONS
        and "sample" not in f.path.lower()
    ]
    return max(candidates, key=lambda f: f.length, default=None)


def _parse_added(data: dict) -> AddedTorrent:
    details = data.get("details") or {}
    files = [
        TorrentFile(i, "/".join(f["components"]), f["length"])
        for i, f in enumerate(details.get("files", []))
    ]
    return AddedTorrent(
        id=data.get("id"),
        info_hash=details.get("info_hash", ""),
        name=details.get("name"),
        output_folder=data.get("output_folder", ""),
        files=files,
    )


class RqbitClient:
    def __init__(self, base_url: str, http: httpx.AsyncClient):
        self._base = base_url.rstrip("/")
        self._http = http

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        resp = await self._http.request(method, self._base + path, **kwargs)
        if not resp.is_success:
            raise RqbitError(resp.status_code, resp.text)
        return resp

    @staticmethod
    def _body(source: TorrentSource) -> bytes:
        return source.encode() if isinstance(source, str) else source

    async def list_files(self, source: TorrentSource) -> AddedTorrent:
        resp = await self._request(
            "POST", "/torrents", params={"list_only": "true"}, content=self._body(source)
        )
        return _parse_added(resp.json())

    async def add(self, source: TorrentSource, *, only_file: int,
                  output_folder: str) -> AddedTorrent:
        resp = await self._request(
            "POST", "/torrents",
            params={"overwrite": "true", "only_files": str(only_file),
                    "output_folder": output_folder},
            content=self._body(source),
        )
        added = _parse_added(resp.json())
        if added.id is None:
            raise RqbitError(resp.status_code, "rqbit returned no torrent id")
        return added

    async def stats(self, torrent_id: int) -> TorrentStats:
        data = (await self._request("GET", f"/torrents/{torrent_id}/stats/v1")).json()
        live = data.get("live") or {}
        remaining = live.get("time_remaining")
        mbps = (live.get("download_speed") or {}).get("mbps", 0) or 0
        return TorrentStats(
            state=data.get("state", ""),
            progress_bytes=data.get("progress_bytes", 0),
            total_bytes=data.get("total_bytes", 0),
            finished=bool(data.get("finished")),
            error=data.get("error"),
            download_bps=mbps * MIB,
            live_peers=((live.get("snapshot") or {}).get("peer_stats") or {}).get("live", 0),
            eta_s=int(remaining["duration"]["secs"]) if remaining else None,
        )

    async def pause(self, torrent_id: int) -> None:
        await self._request("POST", f"/torrents/{torrent_id}/pause")

    async def delete(self, torrent_id: int) -> None:
        await self._request("POST", f"/torrents/{torrent_id}/delete")

    def stream_url(self, torrent_id: int, file_index: int) -> str:
        return f"{self._base}/torrents/{torrent_id}/stream/{file_index}"
