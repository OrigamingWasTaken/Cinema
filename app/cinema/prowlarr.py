import asyncio
from urllib.parse import quote

import httpx

from cinema.releases import Release
from cinema.rqbit import TorrentSource

TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.stealth.si:80/announce",
    "udp://tracker.torrent.eu.org:451/announce",
    "udp://exodus.desync.com:6969/announce",
)


SEARCH_TIMEOUT = 60.0  # Prowlarr waits for all of its indexers
RESOLVE_TIMEOUT = 30.0


class ProwlarrError(Exception):
    pass


def _release(item: dict) -> Release:
    return Release(
        guid=item["guid"],
        title=item["title"],
        size=item.get("size") or 0,
        seeders=item.get("seeders") or 0,
        indexer=item.get("indexer") or "",
        download_url=item.get("downloadUrl"),
        magnet=item.get("magnetUrl"),
        info_hash=item.get("infoHash"),
    )


def _built_magnet(r: Release) -> str:
    trackers = "".join(f"&tr={quote(t, safe='')}" for t in TRACKERS)
    return f"magnet:?xt=urn:btih:{r.info_hash}&dn={quote(r.title, safe='')}{trackers}"


class ProwlarrClient:
    def __init__(self, base_url: str, api_key: str, http: httpx.AsyncClient):
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._http = http

    def _require_key(self) -> None:
        if not self._key:
            raise ProwlarrError("prowlarr api key is not configured")

    async def _search_one(self, query: str) -> list[dict]:
        try:
            resp = await self._http.get(
                f"{self._base}/api/v1/search",
                params={"query": query, "type": "movie", "categories": 2000, "limit": 100},
                headers={"X-Api-Key": self._key},
                timeout=SEARCH_TIMEOUT,
            )
        except httpx.HTTPError as e:
            raise ProwlarrError(f"prowlarr unreachable: {type(e).__name__}") from None
        if resp.status_code >= 300:
            raise ProwlarrError(f"prowlarr search failed: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            raise ProwlarrError("prowlarr search returned invalid JSON") from None
        if not isinstance(data, list):
            raise ProwlarrError("prowlarr search returned unexpected payload")
        return data

    async def search(self, queries: list[str]) -> list[Release]:
        self._require_key()
        results = await asyncio.gather(*(self._search_one(q) for q in queries))
        seen: set[str] = set()
        out: list[Release] = []
        for items in results:
            for item in items:
                if item.get("protocol") != "torrent":
                    continue
                r = _release(item)
                key = r.info_hash.lower() if r.info_hash else r.guid
                if key in seen:
                    continue
                seen.add(key)
                out.append(r)
        return out

    async def _fetch(self, url: str) -> TorrentSource:
        """Follow the download link; any failure raises ProwlarrError without the URL."""
        for hop in range(2):
            try:
                resp = await self._http.get(url, follow_redirects=False,
                                          timeout=RESOLVE_TIMEOUT)
            except httpx.HTTPError as e:
                raise ProwlarrError(f"prowlarr download failed: {type(e).__name__}") from None
            if 300 <= resp.status_code < 400:
                location = resp.headers.get("location", "")
                if location.startswith("magnet:"):
                    return location
                if location.startswith("http") and hop == 0:
                    url = location
                    continue
                raise ProwlarrError("prowlarr download failed: unusable redirect")
            if resp.status_code != 200:
                raise ProwlarrError(f"prowlarr download failed: HTTP {resp.status_code}")
            if resp.content.startswith(b"d"):
                return resp.content
            raise ProwlarrError("prowlarr download failed: not a torrent file")
        raise ProwlarrError("prowlarr download failed: too many redirects")

    async def resolve(self, r: Release) -> TorrentSource:
        self._require_key()
        if r.magnet and r.magnet.startswith("magnet:"):
            return r.magnet
        url = r.download_url or (r.magnet if r.magnet and r.magnet.startswith("http") else None)
        error: ProwlarrError | None = None
        if url:
            try:
                return await self._fetch(url)
            except ProwlarrError as e:
                error = e
        if r.info_hash:
            return _built_magnet(r)
        raise error or ProwlarrError("prowlarr release has no download source")
