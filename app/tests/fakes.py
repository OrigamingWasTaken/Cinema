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

    add_torrent() registers a torrent with its own files and scripted stats
    (per-file progress, finished, live peers) without holding any content.
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
        # When set, stream requests wait for it before answering (rqbit waiting for
        # pieces); requests cancelled while waiting are recorded in cancelled_opens.
        self.stream_gate: asyncio.Event | None = None
        self.cancelled_opens: list[tuple[int, int]] = []
        self._next_id = 0
        self.transport = httpx.MockTransport(self._handle)

    def add_torrent(self, info_hash: str, files: list[tuple[list[str], int]], *,
                    file_progress: list[int] | None = None, finished: bool = False,
                    live_peers: int = 1, name: str = "Show") -> int:
        tid = self._next_id
        self._next_id += 1
        self.torrents[tid] = {"id": tid, "info_hash": info_hash, "name": name,
                              "output_folder": "/downloads", "only_files": None,
                              "paused": False, "deleted": False, "body": b"",
                              "files": files,
                              "file_progress": file_progress or [0] * len(files),
                              "finished": finished, "live_peers": live_peers}
        return tid

    # -- helpers ---------------------------------------------------------
    def _details(self, only: set[int] | None = None,
                 files: list[tuple[list[str], int]] | None = None,
                 info_hash: str | None = None, name: str | None = None) -> dict:
        return {
            "info_hash": info_hash or self.info_hash,
            "name": name or self.name,
            "files": [
                {"name": c[-1], "components": c, "length": n,
                 "included": only is None or i in only}
                for i, (c, n) in enumerate(self.files if files is None else files)
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

        if request.method == "GET" and path == "/torrents":
            return self._json(200, {"torrents": [
                {"id": t["id"], "info_hash": t["info_hash"], "name": t.get("name", self.name),
                 "output_folder": t["output_folder"]}
                for t in self.torrents.values() if not t["deleted"]]})
        m = re.fullmatch(r"/torrents/(\d+)(?:/(stats/v1|pause|delete|stream/(\d+)))?", path)
        if not m:
            return httpx.Response(404, text="not found")
        tid = int(m.group(1))
        if tid not in self.torrents:
            return httpx.Response(404, text="torrent not found")
        action = m.group(2)
        if action is None:
            t = self.torrents[tid]
            return self._json(200, self._details(t["only_files"], t.get("files"),
                                                 t["info_hash"], t.get("name")))
        if action == "stats/v1":
            return self._json(200, self._stats(tid))
        if action == "pause":
            self.torrents[tid]["paused"] = True
            return self._json(200, {})
        if action == "delete":
            self.torrents[tid]["deleted"] = True
            return self._json(200, {})
        idx = int(m.group(3))
        if self.stream_gate is not None:
            try:
                await self.stream_gate.wait()
            except asyncio.CancelledError:
                self.cancelled_opens.append((tid, idx))
                raise
        return self._stream(tid, idx, request)

    def _stats(self, tid: int) -> dict:
        t = self.torrents[tid]
        if "file_progress" in t:
            return self._scripted_stats(t)
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
                "file_progress": self._file_progress(tid), "error": None, "progress_bytes": got,
                "uploaded_bytes": 0, "total_bytes": total, "finished": finished,
                "live": live}

    @staticmethod
    def _scripted_stats(t: dict) -> dict:
        progress = list(t["file_progress"])
        live = None
        if not t["paused"]:
            live = {"download_speed": {"mbps": 0.0 if t["finished"] else 2.0},
                    "time_remaining": None if t["finished"] else {"duration": {"secs": 90}},
                    "snapshot": {"peer_stats": {"live": t["live_peers"]}}}
        return {"state": "paused" if t["paused"] else "live", "file_progress": progress,
                "error": None, "progress_bytes": sum(progress), "uploaded_bytes": 0,
                "total_bytes": sum(n for _, n in t["files"]), "finished": t["finished"],
                "live": live}

    def _file_progress(self, tid: int) -> list[int]:
        out = []
        for i in range(len(self.files)):
            data = self.content.get((tid, i))
            out.append(0 if data is None
                       else min(self.available.get((tid, i), len(data)), len(data)))
        return out

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


class FakeSonarr:
    """In-memory Sonarr v3 API (the endpoints cinema.sonarr uses).

    lookup: tvdb id -> lookup-result series dict (no "id")
    series: id -> series dict; episodes: list of episode dicts (seriesId, ...)
    files: list of episodefile dicts (seriesId, ...); queue: list of queue records
    profiles: list of {"id", "name"}; calls: every write as (method, path, json)
    """

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key  # when set, requests without it get 401
        self.lookup: dict[int, dict] = {}
        self.series: dict[int, dict] = {}
        self.episodes: list[dict] = []
        self.files: list[dict] = []
        self.queue: list[dict] = []
        self.profiles: list[dict] = [{"id": 1, "name": "Cinema"}]
        self.calls: list[tuple[str, str, object]] = []
        self.requests: list[httpx.Request] = []
        self.fail_status: int | None = None
        self._next_id = 100
        self.transport = httpx.MockTransport(self._handle)

    @staticmethod
    def _json(status: int, body) -> httpx.Response:
        return httpx.Response(status, content=json.dumps(body).encode(),
                              headers={"content-type": "application/json"})

    def _record(self, request: httpx.Request):
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        return body

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_status is not None:
            return httpx.Response(self.fail_status, text="boom")
        if self.api_key is not None and request.headers.get("x-api-key") != self.api_key:
            return httpx.Response(401, text="unauthorized")
        path, method = request.url.path, request.method
        params = dict(request.url.params)

        if method == "GET" and path == "/api/v3/series/lookup":
            term = params.get("term", "")
            found = self.lookup.get(int(term[5:])) if term.startswith("tvdb:") else None
            return self._json(200, [found] if found else [])
        if path == "/api/v3/series":
            if method == "GET":
                return self._json(200, list(self.series.values()))
            if method == "POST":
                body = self._record(request)
                self._next_id += 1
                added = {**body, "id": self._next_id}
                self.series[added["id"]] = added
                return self._json(201, added)
        m = re.fullmatch(r"/api/v3/series/(\d+)", path)
        if m and method == "PUT":
            body = self._record(request)
            self.series[int(m.group(1))] = body
            return self._json(202, body)
        if method == "GET" and path == "/api/v3/episode":
            sid = int(params["seriesId"])
            return self._json(200, [e for e in self.episodes if e.get("seriesId") == sid])
        if method == "PUT" and path == "/api/v3/episode/monitor":
            body = self._record(request)
            for e in self.episodes:
                if e["id"] in body["episodeIds"]:
                    e["monitored"] = body["monitored"]
            return self._json(202, [e for e in self.episodes if e["id"] in body["episodeIds"]])
        if method == "POST" and path == "/api/v3/command":
            body = self._record(request)
            return self._json(201, {"id": 1, "name": body["name"], "status": "queued"})
        if method == "GET" and path == "/api/v3/episodefile":
            sid = int(params["seriesId"])
            return self._json(200, [f for f in self.files if f.get("seriesId") == sid])
        if method == "DELETE" and path == "/api/v3/episodefile/bulk":
            body = self._record(request)
            gone = set(body["episodeFileIds"])
            self.files = [f for f in self.files if f["id"] not in gone]
            for e in self.episodes:
                if e.get("episodeFileId") in gone:
                    e["hasFile"], e["episodeFileId"] = False, 0
            return self._json(200, {})
        if method == "GET" and path == "/api/v3/queue":
            return self._json(200, {"page": 1, "pageSize": int(params.get("pageSize", 10)),
                                    "totalRecords": len(self.queue),
                                    "records": self.queue})
        m = re.fullmatch(r"/api/v3/queue/(\d+)", path)
        if m and method == "DELETE":
            self.calls.append((method, path, params))
            self.queue = [r for r in self.queue if r["id"] != int(m.group(1))]
            return httpx.Response(200)
        if method == "GET" and path == "/api/v3/qualityprofile":
            return self._json(200, self.profiles)
        return httpx.Response(404, text="not found")
