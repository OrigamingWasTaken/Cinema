from dataclasses import dataclass, field
from pathlib import PurePosixPath

import httpx

TorrentSource = str | bytes
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi"}
MIB = 1048576


def _eta_secs(remaining) -> int | None:
    secs = ((remaining or {}).get("duration") or {}).get("secs")
    return int(secs) if secs is not None else None


# Magnet metadata over DHT can take tens of seconds.
METADATA_TIMEOUT = 120.0


class RqbitError(Exception):
    def __init__(self, status: int | None, text: str):
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
    file_progress: list[int] = field(default_factory=list)


@dataclass
class TorrentSummary:
    id: int
    info_hash: str
    name: str | None
    output_folder: str


def is_video_file(f: TorrentFile) -> bool:
    """A video file that is not a sample."""
    return (PurePosixPath(f.path).suffix.lower() in VIDEO_EXTENSIONS
            and "sample" not in f.path.lower())


def pick_video_file(files: list[TorrentFile]) -> TorrentFile | None:
    candidates = [f for f in files if is_video_file(f)]
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

    @property
    def http(self) -> httpx.AsyncClient:
        return self._http

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            resp = await self._http.request(method, self._base + path, **kwargs)
        except httpx.HTTPError as e:
            raise RqbitError(None, f"{type(e).__name__}: {e}") from e
        if not resp.is_success:
            raise RqbitError(resp.status_code, resp.text)
        return resp

    @staticmethod
    def _body(source: TorrentSource) -> bytes:
        return source.encode() if isinstance(source, str) else source

    async def list_files(self, source: TorrentSource) -> AddedTorrent:
        resp = await self._request(
            "POST", "/torrents", params={"list_only": "true"}, content=self._body(source),
            timeout=METADATA_TIMEOUT,
        )
        return _parse_added(resp.json())

    async def add(self, source: TorrentSource, *, only_file: int,
                  output_folder: str) -> AddedTorrent:
        resp = await self._request(
            "POST", "/torrents",
            params={"overwrite": "true", "only_files": str(only_file),
                    "output_folder": output_folder},
            content=self._body(source), timeout=METADATA_TIMEOUT,
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
            eta_s=_eta_secs(remaining),
            file_progress=list(data.get("file_progress") or []),
        )

    async def list_torrents(self) -> list[TorrentSummary]:
        data = (await self._request("GET", "/torrents")).json()
        return [
            TorrentSummary(
                id=t["id"],
                info_hash=t.get("info_hash", ""),
                name=t.get("name"),
                output_folder=t.get("output_folder", ""),
            )
            for t in data.get("torrents", [])
        ]

    async def files(self, torrent_id: int) -> list[TorrentFile]:
        data = (await self._request("GET", f"/torrents/{torrent_id}")).json()
        return [
            TorrentFile(i, "/".join(f["components"]), f["length"])
            for i, f in enumerate(data.get("files", []))
        ]

    async def pause(self, torrent_id: int) -> None:
        await self._request("POST", f"/torrents/{torrent_id}/pause")

    async def delete(self, torrent_id: int) -> None:
        await self._request("POST", f"/torrents/{torrent_id}/delete")

    def stream_url(self, torrent_id: int, file_index: int) -> str:
        return f"{self._base}/torrents/{torrent_id}/stream/{file_index}"
