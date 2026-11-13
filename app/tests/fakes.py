"""In-memory rqbit used by the test suites (rqbit client, WebDAV, API)."""
import asyncio
import json
import re
from urllib.parse import unquote

import httpx

CHUNK = 64 * 1024


class _TrackedStream(httpx.AsyncByteStream):
    def __init__(self, data: bytes, owner: "FakeRqbit", key: tuple[int, int]):
        self._data = data
        self._owner = owner
        self._key = key
        self._closed = False

    async def __aiter__(self):
        size = self._owner.chunk_size
        for i in range(0, len(self._data), size):
            yield self._data[i : i + size]
            await asyncio.sleep(0)  # let a client disconnect cancel the consumer

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            self._owner.closed_streams.append(self._key)


class FakeRqbit:
    """Mimics the rqbit 9.0.1 HTTP API.

    torrents: id -> torrent record (only real adds are registered)
    content: (id, file_idx) -> full file bytes
    available: (id, file_idx) -> bytes downloaded so far (default: all)
    """

    def __init__(self, files: list[tuple[list[str], int]] | None = None,
                 info_hash: str = "a" * 40, name: str = "Sintel"):
        self.files = files or []
        self.info_hash = info_hash
        self.name = name
        self.torrents: dict[int, dict] = {}
        self.content: dict[tuple[int, int], bytes] = {}
        self.available: dict[tuple[int, int], int] = {}
        self.requests: list[dict] = []
        self.closed_streams: list[tuple[int, int]] = []
        self.chunk_size = CHUNK
        self._next_id = 0
        self.transport = httpx.MockTransport(self._handle)

    # -- helpers ---------------------------------------------------------
    def _details(self, only: set[int] | None = None) -> dict:
        return {
            "info_hash": self.info_hash,
            "name": self.name,
            "files": [
                {"name": c[-1], "components": c, "length": n,
                 "included": only is None or i in only}
                for i, (c, n) in enumerate(self.files)
            ],
        }

    @staticmethod
    def _json(status: int, body) -> httpx.Response:
        return httpx.Response(status, content=json.dumps(body).encode(),
                              headers={"content-type": "application/json"})

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = dict(request.url.params)
        self.requests.append({"method": request.method, "path": path,
                              "params": params, "headers": dict(request.headers)})

        if request.method == "POST" and path == "/torrents":
            only = ({int(x) for x in params["only_files"].split(",")}
                    if "only_files" in params else None)
            out = params.get("output_folder", "/downloads")
            if params.get("list_only") == "true":
                return self._json(200, {"id": None, "details": self._details(),
                                        "output_folder": out})
            tid = self._next_id
            self._next_id += 1
            self.torrents[tid] = {"id": tid, "info_hash": self.info_hash,
                                  "output_folder": out, "only_files": only,
                                  "paused": False, "deleted": False,
                                  "body": request.content}
            return self._json(200, {"id": tid, "details": self._details(only),
                                    "output_folder": out})

        m = re.fullmatch(r"/torrents/(\d+)/(stats/v1|pause|delete|stream/(\d+))", path)
        if not m:
            return httpx.Response(404, text="not found")
        tid = int(m.group(1))
        if tid not in self.torrents:
            return httpx.Response(404, text="torrent not found")
        action = m.group(2)
        if action == "stats/v1":
            return self._json(200, self._stats(tid))
        if action == "pause":
            self.torrents[tid]["paused"] = True
            return self._json(200, {})
        if action == "delete":
            self.torrents[tid]["deleted"] = True
            return self._json(200, {})
        return self._stream(tid, int(m.group(3)), request)

    def _stats(self, tid: int) -> dict:
        t = self.torrents[tid]
        total = sum(len(b) for (i, _), b in self.content.items() if i == tid)
        got = sum(self.available.get(k, len(b))
                  for k, b in self.content.items() if k[0] == tid)
        finished = total > 0 and got >= total
        live = None
        if not t["paused"] and not finished:
            live = {"download_speed": {"mbps": 1.0},
                    "time_remaining": None,
                    "snapshot": {"peer_stats": {"live": 1}}}
        return {"state": "paused" if t["paused"] else ("live" if live else "finished"),
                "file_progress": [], "error": None, "progress_bytes": got,
                "uploaded_bytes": 0, "total_bytes": total, "finished": finished,
                "live": live}

    def _stream(self, tid: int, idx: int, request: httpx.Request) -> httpx.Response:
        key = (tid, idx)
        if key not in self.content:
            return httpx.Response(404, text="file not found")
        full = self.content[key]
        size = len(full)
        avail = min(self.available.get(key, size), size)
        rng = request.headers.get("range")
        headers = {"Accept-Ranges": "bytes"}
        if rng is None:
            body = full[:avail]
            return httpx.Response(200, headers={**headers, "Content-Length": str(len(body))},
                                  stream=_TrackedStream(body, self, key))
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())
        if not m or (m.group(1) == "" and m.group(2) == ""):
            return httpx.Response(416, headers={"Content-Range": f"bytes */{size}"})
        if m.group(1) == "":
            # Like real rqbit 9.0.1: suffix ranges are ignored, full body from byte 0.
            body = full[:avail]
            return httpx.Response(200, headers={**headers, "Content-Length": str(len(body))},
                                  stream=_TrackedStream(body, self, key))
        else:
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        if start >= size or end < start:
            return httpx.Response(416, headers={"Content-Range": f"bytes */{size}"})
        body = full[start : min(end + 1, avail)]
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(len(body))
        return httpx.Response(206, headers=headers,
                              stream=_TrackedStream(body, self, key))
