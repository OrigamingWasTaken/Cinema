# Cinema v2 (Watch While Downloading) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Radarr/qBittorrent stack with a family-friendly phone catalog app plus an rqbit-backed WebDAV server so Infuse on Apple TV can play movies while they download.

**Architecture:** One new FastAPI container (`cinema`) serves the phone web app, a JSON API, and a read-only WebDAV tree for Infuse. It searches TMDB for the catalog and Prowlarr for releases, classifies and ranks releases itself, hands the chosen one to rqbit, and proxies rqbit's blocking HTTP stream to Infuse while the file is incomplete (serving it from disk once done). State lives in SQLite.

**Tech Stack:** Python 3.12, FastAPI 0.161.0, uvicorn 0.54.0, httpx 0.28.1, guessit 4.4.0, pytest 9.1.1 (with anyio's pytest plugin), stdlib sqlite3; vanilla HTML/CSS/JS frontend; Docker Compose on Synology DSM 7.2; rqbit 9.0.1.

**Spec:** `docs/superpowers/specs/2026-10-22-cinema-streaming-design.md`

## Global Constraints

- Branch `streaming-redesign` (based on `origin/feat/cinema-stack`). Commit messages: one title line, no body, no AI attribution.
- `creds.txt` (line 1 NAS user, line 2 NAS password) is git-ignored. Never print, log, echo, or commit its contents; read it only inside scripts.
- NAS: DS720+ at `192.168.2.191`, DSM 7.2.1, docker needs `sudo`. Data root `/volume1/cinema/data`, v2 configs `/volume1/cinema/config`, v1 compose project `/volume1/cinema/Cinema-feat-cinema-stack`, v2 checkout `/volume1/cinema/Cinema`.
- rqbit image pinned `ikatson/rqbit:9.0.1`; upload limited with `RQBIT_RATELIMIT_UPLOAD=1`; finished torrents paused.
- App port `8888`; WebDAV under `/dav`; tree `/dav/Movies/{Title (Year)}/{Title (Year)}.{ext}`.
- In-container paths: data `/data`, new downloads `/data/movies`, legacy v1 library `/data/media/movies`, DB `/config/cinema.db`, Prowlarr config `/prowlarr-config/config.xml` (read-only).
- `setup.py` stays stdlib-only, runs on the NAS's Python 3.
- LAN only, no logins (optional DAV Basic auth via `DAV_USER`/`DAV_PASSWORD`).
- UI copy in English and French, chosen by `navigator.language` (starts with `fr` → French). The family never sees release names, codecs, or seeder counts.
- App tests run from `app/`: `.venv/bin/python -m pytest -q`. Root tests (setup.py) run from repo root: `python3 -m unittest discover -s tests -q`.

## Review Focus

1. **Range edge cases from Infuse** — open-ended `bytes=N-`, suffix `bytes=-N` (MKV cues at the end), and a range past EOF must give correct `206`/`416` from both the disk path and the rqbit proxy. Test: `test_dav_range_variants` in Task 4.
2. **Infuse disconnects mid-stream** (seek = new request, old one dropped) — the upstream rqbit stream must be closed, not leaked. Test: `test_proxy_closes_upstream_on_disconnect` in Task 4.
3. **Unicode and punctuation in titles** ("Amélie", "Mission: Impossible", "AC/DC") — folder names must be safe, and WebDAV hrefs must be percent-encoded and decode back to the same movie. Test: `test_library_name_sanitizes` (Task 3) and `test_dav_unicode_href_roundtrip` (Task 4).
4. **Double-tap on Recommended / two family members picking the same movie** — the second request must not add a second torrent. Test: `test_download_twice_conflicts` in Task 11.
5. **Prowlarr or TMDB down / slow** — the phone must get a clear error state, not a hang or a 500 page. Test: `test_releases_upstream_error_is_502` in Task 11.

---

## File Structure

```
docker-compose.yml          (rewrite) v2 services: cinema, rqbit, prowlarr, flaresolverr
.env.example                (rewrite) v2 keys
setup.py                    (modify)  v2 setup: dirs, prowlarr migration, launch, prowlarr config
tests/test_setup.py         (modify)  drop qB/Radarr tests, add v2 tests
deploy.sh                   (create)  ship working tree to NAS over SSH, run compose
README.md                   (rewrite) v2 usage, Infuse setup, family workflow
app/
  Dockerfile, requirements.txt, requirements-dev.txt, pytest.ini
  cinema/
    __init__.py
    config.py      Settings + load_settings
    main.py        create_app(), build() factory, lifespan poller
    rqbit.py       RqbitClient + pick_video_file
    store.py       Movie, Store (SQLite), library_name
    webdav.py      build_dav_router, READY_BYTES
    downloads.py   start_download, refresh, run_poller, remove_movie, status_view
    devtools.py    CLI: add a magnet directly (debug + on-device check)
    tmdb.py        MovieInfo, MovieCard, TmdbClient
    releases.py    Release, Classified, classify, matches_movie, language_ok
    ranking.py     recommended, more_groups, score, size_floor_bytes
    prowlarr.py    ProwlarrClient
    api.py         build_api_router (JSON for the phone)
    static/        index.html, app.js, i18n.js, styles.css, manifest.webmanifest, icons
  tests/
    conftest.py, fakes.py, fixtures/releases.json, test_*.py
```

---

### Task 1: App scaffold, config, container and compose

**Files:**
- Create: `app/Dockerfile`, `app/requirements.txt`, `app/requirements-dev.txt`, `app/pytest.ini`, `app/cinema/__init__.py`, `app/cinema/config.py`, `app/cinema/main.py`, `app/tests/conftest.py`, `app/tests/test_config.py`
- Rewrite: `docker-compose.yml`, `.env.example`
- Modify: `.gitignore` (add `app/.venv/`, `.pytest_cache/`)

**Interfaces:**
- Produces:
  - `Settings` (frozen dataclass): `tmdb_token: str`, `tmdb_language: str`, `prowlarr_url: str`, `prowlarr_api_key: str` (may be `""`), `rqbit_url: str`, `movies_dir: Path`, `legacy_dir: Path`, `db_path: Path`, `dav_user: str | None`, `dav_password: str | None`
  - `load_settings(env: Mapping[str, str] = os.environ) -> Settings`
  - `create_app(settings: Settings, **overrides) -> FastAPI` (overrides grow in later tasks: `store`, `rqbit`, `tmdb`, `prowlarr`, `start_poller: bool`); `build() -> FastAPI` (uvicorn factory)
  - `GET /healthz` → `{"ok": true}`
  - `app/tests/conftest.py`: `anyio_backend` fixture returning `"asyncio"`; `settings(tmp_path)` fixture building a `Settings` with tmp dirs.

- [ ] **Step 1: Create venv and dependency files**

`requirements.txt`: `fastapi==0.161.0`, `uvicorn[standard]==0.54.0`, `httpx==0.28.1`, `guessit==4.4.0`. `requirements-dev.txt`: `-r requirements.txt`, `pytest==9.1.1`. `pytest.ini`: `testpaths = tests`.

Run: `cd app && uv venv -p 3.12 .venv && uv pip install -p .venv -r requirements-dev.txt`
Expected: installs without error.

- [ ] **Step 2: Write failing tests** in `app/tests/test_config.py`

```python
def test_defaults(tmp_path):
    s = load_settings({"TMDB_TOKEN": "t"})
    assert s.tmdb_language == "en-US"
    assert s.prowlarr_url == "http://prowlarr:9696"
    assert s.rqbit_url == "http://rqbit:3030"
    assert s.movies_dir == Path("/data/movies")
    assert s.legacy_dir == Path("/data/media/movies")
    assert s.db_path == Path("/config/cinema.db")
    assert s.dav_user is None

def test_missing_tmdb_token_raises():
    with pytest.raises(RuntimeError, match="TMDB_TOKEN"):
        load_settings({})

def test_prowlarr_key_from_config_xml(tmp_path):
    xml = tmp_path / "config.xml"
    xml.write_text("<Config><ApiKey>abc123</ApiKey></Config>")
    s = load_settings({"TMDB_TOKEN": "t", "PROWLARR_CONFIG_XML": str(xml)})
    assert s.prowlarr_api_key == "abc123"

def test_prowlarr_key_env_wins(tmp_path): ...   # PROWLARR_API_KEY="env" beats the xml → "env"
def test_prowlarr_key_missing_is_empty(): ...   # no env, xml path missing → ""

def test_healthz(settings):
    client = TestClient(create_app(settings, start_poller=False))
    assert client.get("/healthz").json() == {"ok": True}
```

- [ ] **Step 3: Run** `cd app && .venv/bin/python -m pytest -q` → FAIL (import errors).

- [ ] **Step 4: Implement `config.py` and `main.py`**

Env names: `TMDB_TOKEN`, `TMDB_LANGUAGE`, `PROWLARR_URL`, `PROWLARR_API_KEY`, `PROWLARR_CONFIG_XML` (default `/prowlarr-config/config.xml`), `RQBIT_URL`, `MOVIES_DIR`, `LEGACY_DIR`, `DB_PATH`, `DAV_USER`, `DAV_PASSWORD`. Parse the XML with `xml.etree.ElementTree`. Ignore a missing or unreadable XML.

- [ ] **Step 5: Run tests** → PASS.

- [ ] **Step 6: Dockerfile and compose**

`app/Dockerfile`: `python:3.12-slim`, `pip install --no-cache-dir -r requirements.txt`, copy `cinema/`, `EXPOSE 8888`, `CMD ["uvicorn", "--factory", "cinema.main:build", "--host", "0.0.0.0", "--port", "8888"]`.

`docker-compose.yml` (all on network `cinema`, `restart: unless-stopped`):
- `cinema`: `build: ./app`, ports `8888:8888`, env `TZ`, `TMDB_TOKEN`, `TMDB_LANGUAGE`, `DAV_USER`, `DAV_PASSWORD`. Volumes `${DATA_PATH}:/data`, `${CONFIG_PATH}/cinema:/config`, `${CONFIG_PATH}/prowlarr:/prowlarr-config:ro`. `depends_on: [rqbit, prowlarr]`.
- `rqbit`: `image: ikatson/rqbit:9.0.1`, `command: ["server", "start", "/data/movies"]`, env `RQBIT_RATELIMIT_UPLOAD=1`, `RQBIT_UPNP_PORT_FORWARD_DISABLE=true`, `RQBIT_LISTEN_PORT=4240`. Volumes `${DATA_PATH}:/data`, `${CONFIG_PATH}/rqbit/db:/home/rqbit/db`, `${CONFIG_PATH}/rqbit/cache:/home/rqbit/cache`. Ports `4240:4240`, `4240:4240/udp`, `127.0.0.1:3030:3030` (debug only).
- `prowlarr`: as v1, but volume `${CONFIG_PATH}/prowlarr:/config`, port `9696:9696`.
- `flaresolverr`: as v1, without a published port.

`.env.example`: `PUID`, `PGID`, `TZ=Europe/Zurich`, `DATA_PATH=/volume1/cinema/data`, `CONFIG_PATH=/volume1/cinema/config`, `TMDB_TOKEN=`, `TMDB_LANGUAGE=en-US`, `DAV_USER=`, `DAV_PASSWORD=`, `V1_CONFIG_PATH=/volume1/cinema/Cinema-feat-cinema-stack/config`. Each key gets a one-line comment.

Docker isn't installed on the dev PC; the compose file is validated by the first NAS deploy in Task 5.

- [ ] **Step 7: Commit** `feat: scaffold cinema app container and v2 compose`

---

### Task 2: rqbit client

**Files:**
- Create: `app/cinema/rqbit.py`, `app/tests/fakes.py`, `app/tests/test_rqbit.py`

**Interfaces:**
- Produces:
  - `TorrentFile(index: int, path: str, length: int)`: `path` is `"/".join(components)`
  - `AddedTorrent(id: int | None, info_hash: str, name: str | None, output_folder: str, files: list[TorrentFile])`
  - `TorrentStats(state: str, progress_bytes: int, total_bytes: int, finished: bool, error: str | None, download_bps: float, live_peers: int, eta_s: int | None)`
  - `TorrentSource = str | bytes` (magnet text, or `.torrent` bytes)
  - `class RqbitClient(base_url: str, http: httpx.AsyncClient)`, with:
    - `async list_files(source) -> AddedTorrent`
    - `async add(source, *, only_file: int, output_folder: str) -> AddedTorrent`
    - `async stats(torrent_id: int) -> TorrentStats`
    - `async pause(torrent_id: int) -> None`
    - `async delete(torrent_id: int) -> None`
    - `stream_url(torrent_id: int, file_index: int) -> str`
  - `pick_video_file(files: list[TorrentFile]) -> TorrentFile | None`: the largest file with extension `.mkv/.mp4/.m4v/.avi`, skipping any path containing `sample` (case-insensitive)
  - `tests/fakes.py`: `FakeRqbit`, an in-memory rqbit exposing `.transport` (an `httpx.MockTransport`), with `torrents: dict[int, dict]`, `content: dict[(int, int), bytes]`, and `available: dict[(int, int), int]`, the bytes "downloaded so far". The stream handler honours `Range` and serves only up to `available`, mimicking the blocking stream with a full-size `Content-Range`. Later tasks reuse it.

rqbit 9.0.1 API facts (from source):
- `POST /torrents` takes the body as magnet text or torrent bytes. Query params: `list_only=true`, `overwrite=true`, `output_folder=...`, `only_files=3` (comma-separated). Response: `{"id", "details": {"info_hash", "name", "files": [{"name", "components", "length", "included"}]}, "output_folder"}`.
- `GET /torrents/{id}/stats/v1` returns `{"state", "file_progress", "error", "progress_bytes", "uploaded_bytes", "total_bytes", "finished", "live": null | {"download_speed": {"mbps"}, "time_remaining": null | {"duration": {"secs"}}, "snapshot": {"peer_stats": {"live"}}}}`. Treat `mbps` as MiB/s (`download_bps = mbps * 1048576`); Task 5 confirms this against the real server.
- `POST /torrents/{id}/pause` and `POST /torrents/{id}/delete`.
- Stream: `GET /torrents/{id}/stream/{file_idx}`.

- [ ] **Step 1: Failing tests**: `test_add_sends_only_files_and_output_folder` (asserts the query string has `only_files=2`, `output_folder=/data/movies/Sintel (2010)`, `overwrite=true`), `test_list_files_parses_components`, `test_stats_live_and_paused` (`live: null` → `download_bps == 0`, `live_peers == 0`, `eta_s is None`), `test_pick_video_file_skips_sample_and_nonvideo`, `test_stream_url`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** with `httpx`. Non-2xx → raise `RqbitError(status, text)`.
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: add rqbit API client`

---

### Task 3: SQLite store and library naming

**Files:**
- Create: `app/cinema/store.py`, `app/tests/test_store.py`

**Interfaces:**
- Produces:
  - `Movie` (dataclass): `tmdb_id: int`, `title: str`, `year: int | None`, `poster_path: str | None`, `runtime_min: int | None`, `release_name: str`, `info_hash: str`, `torrent_id: int`, `file_index: int`, `file_path: str` (absolute on-disk path inside the container), `size: int`, `status: str` (`"downloading" | "done" | "failed"`), `progress_bytes: int = 0`, `download_bps: float = 0.0`, `eta_s: int | None = None`, `added_at: float = 0.0`, `last_progress_at: float = 0.0`
  - `library_name(title: str, year: int | None) -> str`: `"Title (Year)"`. Replace any of `/ \ : * ? " < > |` with `-` (but `": "` becomes `" - "`), collapse whitespace, strip trailing dots and spaces. Omit `" (Year)"` when `year` is None.
  - `class Store(path: Path)`: creates the schema on init, uses `check_same_thread=False` and a `threading.Lock`. Methods:
    - `upsert(movie: Movie) -> None`
    - `get(tmdb_id: int) -> Movie | None`
    - `all() -> list[Movie]`, ordered by `added_at` desc
    - `delete(tmdb_id: int) -> None`

- [ ] **Step 1: Failing tests**
  - `test_upsert_get_roundtrip`
  - `test_all_orders_newest_first`
  - `test_delete`
  - `test_library_name_sanitizes`:

```python
assert library_name("Mission: Impossible", 1996) == "Mission - Impossible (1996)"
assert library_name("AC/DC: Let There Be Rock", 1980) == "AC-DC - Let There Be Rock (1980)"
assert library_name("Amélie", 2001) == "Amélie (2001)"
assert library_name("Untitled...", None) == "Untitled"
```
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: add SQLite movie store`

---

### Task 4: WebDAV server for Infuse

**Files:**
- Create: `app/cinema/webdav.py`, `app/tests/test_webdav.py`
- Modify: `app/cinema/main.py` (mount the router; `create_app` accepts `store` and `rqbit` overrides)

**Interfaces:**
- Consumes: `Store`, `Movie`, `library_name` (Task 3); `RqbitClient.stream_url` (Task 2); `FakeRqbit` (Task 2).
- Produces:
  - `build_dav_router(store: Store, rqbit: RqbitClient, http: httpx.AsyncClient, legacy_dir: Path, auth: tuple[str, str] | None) -> APIRouter`, mounted at `/dav`
  - `READY_BYTES = 100 * 1024**2`, defined here and imported by Task 10. A downloading movie is listed only once `progress_bytes >= READY_BYTES`. Done movies are always listed.

Behaviour (from the spec):
- `OPTIONS` on any path returns `DAV: 1`, `Allow: OPTIONS, PROPFIND, GET, HEAD`.
- `PROPFIND` on `/dav/`, `/dav/Movies/`, `/dav/Movies/{name}/`, and the file path returns `207` with `application/xml; charset=utf-8`. Depth `0` returns self only. Depth `1` (or `infinity`, treated as 1) returns self plus children. The props are `displayname`, `resourcetype` (`<collection/>` for dirs), `getcontentlength` (the final `Movie.size` even while downloading), `getcontenttype` (`video/x-matroska` for `.mkv`, `video/mp4` for `.mp4`/`.m4v`, `video/x-msvideo` for `.avi`), and `getlastmodified` (RFC 1123 from `added_at`). Hrefs are percent-encoded with `urllib.parse.quote`.
- File name: `{library_name}{ext}`, where `ext` is the suffix of `file_path`. Legacy entries are each subdirectory of `legacy_dir` holding a video file, listed under its own directory and file names. A legacy name colliding with a DB name loses.
- `GET`/`HEAD` on a file:
  - Done or legacy: `starlette.responses.FileResponse`, which handles Range.
  - Downloading: stream from `rqbit.stream_url(...)` with the client's `Range` header passed through, `timeout=httpx.Timeout(10, read=None)`. Copy back the status, `Content-Range`, `Content-Length`, and `Accept-Ranges`. Set `Content-Type` from the table above. Use a `StreamingResponse` whose generator runs inside `async with http.stream(...)`, so a client disconnect closes upstream.
- Unknown path → `404`. Other methods → `405`. When `auth` is set, every request requires Basic auth (else `401` with `WWW-Authenticate: Basic realm="Cinema"`).

- [ ] **Step 1: Failing tests** (use `TestClient(create_app(settings, store=..., rqbit=RqbitClient(..., httpx.AsyncClient(transport=fake.transport)), start_poller=False))`)
  - `test_propfind_root_lists_movies_dir`
  - `test_propfind_movies_hides_not_ready`: a downloading movie with `progress_bytes = READY_BYTES - 1` is absent, and present at `READY_BYTES`
  - `test_propfind_reports_final_size_while_downloading`: `getcontentlength == movie.size` while the fake has only 1 MB available
  - `test_get_done_file_range_from_disk`: `Range: bytes=10-19` returns `206` and the exact bytes
  - `test_get_downloading_proxies_range_to_rqbit`: the fake receives the same Range header, and the response is `206` with the fake's `Content-Range`
  - `test_dav_range_variants`, for both disk and proxy: `bytes=N-` → `206` to EOF; `bytes=-500` → the last 500 bytes; `bytes=SIZE-` → `416`
  - `test_proxy_closes_upstream_on_disconnect`: read one chunk from a streamed proxy response, close it, assert the fake's response stream `aclose` was called (the fake records closures)
  - `test_dav_unicode_href_roundtrip`: a movie "Amélie" (2001) appears with href `/dav/Movies/Am%C3%A9lie%20%282001%29/`, and GET on the decoded path returns the file
  - `test_legacy_dir_listed`
  - `test_basic_auth_required_when_configured`
  - `test_unsupported_method_405` (`PUT`)
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Build the XML with `xml.etree.ElementTree` in the `DAV:` namespace (prefix `D`).
- [ ] **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: serve library to Infuse over WebDAV`

---

### Task 5: Direct-add CLI, deploy script, and on-device check (gate)

This task retires the spec's four risks. **Do not start Task 6 until the user confirms the Infuse check passed.**

**Files:**
- Create: `app/cinema/downloads.py` (only `start_download` for now), `app/cinema/devtools.py`, `app/tests/test_downloads_start.py`, `deploy.sh`

**Interfaces:**
- Consumes: `RqbitClient`, `pick_video_file` (Task 2); `Store`, `Movie`, `library_name` (Task 3).
- Produces:
  - `async start_download(store: Store, rqbit: RqbitClient, movies_dir: Path, *, tmdb_id: int, title: str, year: int | None, poster_path: str | None, runtime_min: int | None, release_name: str, source: TorrentSource) -> Movie`. It lists files, picks the video file, and adds with `only_file` and `output_folder = movies_dir / library_name(title, year)`. It then upserts a `Movie` with `status="downloading"` and `file_path = output_folder / file.path`. If `pick_video_file` returns None it raises `NoVideoFileError`. If `store.get(tmdb_id)` exists it raises `AlreadyInLibraryError`.
  - CLI: `python -m cinema.devtools add --tmdb-id N --title T --year Y (--magnet M | --torrent-url U)`. It loads settings and calls `start_download` (for `--torrent-url`, it GETs the URL and passes the bytes).
  - `deploy.sh [service...]`: `tar` the working tree (excluding `.git`, `app/.venv`, `creds.txt`, `.env`, `config`, `__pycache__`) and pipe it over `ssh` to `/volume1/cinema/Cinema` on `192.168.2.191`. Then run `sudo -S -p '' docker compose up -d --build --no-deps <services>` there, feeding the sudo password from line 2 of `creds.txt` on stdin (never as an argument and never echoed). The user comes from line 1. SSH auth uses a key: if `ssh -o BatchMode=yes` fails, print the one-time `ssh-copy-id` command for the user to run and exit 1.

- [ ] **Step 1: Failing tests**: `test_start_download_picks_video_and_folder`, `test_start_download_no_video_raises`, `test_start_download_twice_raises`.
- [ ] **Step 2–4:** run → FAIL, implement, run → PASS.
- [ ] **Step 5: Commit** `feat: add direct-add devtool and NAS deploy script`
- [ ] **Step 6: First deploy.** Ask the user to run `! ssh-copy-id <user>@192.168.2.191` if needed (DSM requires the user home service on). Then:
  - Check port 8888 is free on the NAS: `ssh … 'sudo -S ss -ltn | grep :8888'` should print nothing.
  - Create `/volume1/cinema/config/{cinema,rqbit/db,rqbit/cache}` and `/volume1/cinema/data/movies`.
  - Write a minimal `.env` on the NAS (`TMDB_TOKEN=spike`, the rest from `.env.example`).
  - Run `./deploy.sh rqbit cinema`.

  Expected: `curl -s http://192.168.2.191:8888/healthz` → `{"ok":true}`.
- [ ] **Step 7: Verify rqbit facts** on the NAS:
  - `curl -s 127.0.0.1:3030/torrents/<id>/stats/v1` during a download. Confirm the `mbps` unit (compare with the web UI at `:3030/web/` via an SSH tunnel) and confirm `uploaded_bytes` stays ~0.
  - Fix the `TorrentStats` parsing and its test if a field differs, and commit.
- [ ] **Step 8: Add a legal test movie**: `sudo docker compose exec cinema python -m cinema.devtools add --tmdb-id 45745 --title Sintel --year 2010 --magnet "<Sintel magnet from webtorrent.io/free-torrents>"`.
- [ ] **Step 9: Human check on the Apple TV (stop and ask the user).** In Infuse: Add Share → WebDAV → `192.168.2.191`, port `8888`, path `/dav`. The user plays Sintel while it is under 30% downloaded, seeks to about 80%, and reports:
  - whether playback buffered and then resumed cleanly, with no garbage frames
  - any timeout error
  - whether the library scan triggered big downloads (watch `progress_bytes` before and after adding the share)

  If seeking far ahead fails because of an Infuse timeout, record it in the spec's risks section. The fallback is to raise `READY_BYTES` and to show "may pause to buffer" more aggressively; it does not change architecture. Proceed only after the user says it works.
- [ ] **Step 10: RAM check**: `sudo docker stats --no-stream` on the NAS. Note the cinema and rqbit memory in the PR description later.

---

### Task 6: TMDB client

**Files:**
- Create: `app/cinema/tmdb.py`, `app/tests/test_tmdb.py`

**Interfaces:**
- Produces:
  - `MovieCard(id: int, title: str, year: int | None, poster_path: str | None)`
  - `MovieInfo(id: int, imdb_id: str | None, title: str, original_title: str, year: int | None, runtime: int | None, original_language: str, overview: str, poster_path: str | None, backdrop_path: str | None)`
  - `class TmdbClient(token: str, language: str, http: httpx.AsyncClient)`, base `https://api.themoviedb.org/3`, header `Authorization: Bearer <token>`. Methods:
    - `async trending() -> list[MovieCard]` (`/trending/movie/week`)
    - `async popular() -> list[MovieCard]` (`/movie/popular`)
    - `async search(query: str) -> list[MovieCard]` (`/search/movie`, `include_adult=false`)
    - `async movie(tmdb_id: int) -> MovieInfo` (`/movie/{id}`)
  - `TmdbError` on non-2xx or a transport error.
  - In-memory TTL cache: 1 h for `trending`, `popular`, `movie`; 10 min for `search`. Pass `now: Callable[[], float] = time.monotonic` in the constructor for tests.

- [ ] **Step 1: Failing tests** (MockTransport):
  - `test_movie_parses_fields`: `release_date "2024-02-27"` → `year 2024`; empty `release_date` → `None`
  - `test_search_passes_query_and_language`
  - `test_trending_cached_for_an_hour`: the second call within 3599 s makes no request, and the call at 3601 s does
  - `test_error_raises_tmdb_error`
- [ ] **Steps 2–4:** FAIL → implement → PASS. **Step 5: Commit** `feat: add TMDB catalog client`

---

### Task 7: Release classification

**Files:**
- Create: `app/cinema/releases.py`, `app/tests/fixtures/releases.json`, `app/tests/test_releases.py`

**Interfaces:**
- Consumes: `MovieInfo` (Task 6).
- Produces:
  - `Release` (frozen): `guid: str`, `title: str`, `size: int`, `seeders: int`, `indexer: str`, `download_url: str | None`, `magnet: str | None`, `info_hash: str | None`
  - `Classified` (frozen): `release: Release`, `resolution: int | None` (720/1080/1440/2160; `4K`/`UHD` → 2160), `source: str` (`"remux" | "bluray" | "webdl" | "webrip" | "hdtv" | "other"`), `atmos: bool`, `channels: str | None` (`"5.1"`, `"7.1"`, `"2.0"`), `lang: str` (`"original" | "multi" | "french_subs" | "french_only" | "dubbed"`), `group: str | None`, `is_cam: bool` (guessit source Camera / Telesync / Telecine / Screener / Workprint, or tokens `HDCAM`, `CAM`, `TS`, `TC`, `SCR`, `R5`), `is_3d: bool`, `dv_only: bool` (a `DV`/`DoVi` token, or guessit "Dolby Vision", with no `HDR`/`HDR10`/`HDR10+`), `parsed_title: str`, `parsed_year: int | None`
  - `classify(r: Release) -> Classified`: guessit plus a token layer. Tokens are the title split on `[.\s_\-\[\]()]+`, compared case-insensitively.
    - Language precedence: `DUBBED` → dubbed; any of `MULTI`/`MULTi`/`DUAL` → multi; any of `FRENCH`/`TRUEFRENCH`/`VFF`/`VFQ`/`VF`/`VFI` → french_only; `VOSTFR`/`SUBFRENCH` → french_subs; else original.
    - `ATMOS` token → `atmos`.
  - `matches_movie(c: Classified, movie: MovieInfo) -> bool`: compare normalized titles (NFKD, strip accents, lowercase, `&` → `and`, drop non-alphanumerics) against `title` or `original_title`. If both years are known, they must be within ±1.
  - `language_ok(c: Classified, original_language: str) -> bool`: `dubbed` → False. When `original_language == "fr"`, everything else is allowed. Otherwise `french_only` → False.

- [ ] **Step 1: Write the fixture** `releases.json`: a list of `{title, size_gb, seeders, expect: {resolution, source, atmos, lang, is_cam, dv_only}}` with at least these entries (size GB / seeders). Dune: Part Two runtime = 166 min, which puts the 1080p floor at 7.47 GB:

```
Dune.Part.Two.2024.1080p.BluRay.x264-SPARKS                                   14.5 / 420
Dune.Part.Two.2024.1080p.WEB-DL.DDP5.1.Atmos.H.264-FLUX                        9.8 / 800
Dune.Part.Two.2024.2160p.UHD.BluRay.x265.10bit.HDR.TrueHD.7.1.Atmos-SWTYBLZ   62.0 / 90
Dune.Part.Two.2024.1080p.BluRay.REMUX.AVC.TrueHD.7.1.Atmos-FGT                38.0 / 150
Dune.Part.Two.2024.HDCAM.x264-NoGroup                                          1.5 / 30
Dune.Part.Two.2024.1080p.BluRay.x264.AAC5.1-[YTS.MX]                           2.9 / 2000
Dune.Part.Two.2024.MULTi.1080p.BluRay.x264-QTZ                                12.0 / 60
Dune.Part.Two.2024.FRENCH.1080p.WEB.H264-FW                                    5.5 / 40
Dune.Part.Two.2024.2160p.WEB-DL.DV.H265-XYZ                                   18.0 / 70
Dune.Part.Two.2024.1440p.WEB-DL.DDP5.1.H.264-GRP                              11.0 / 25
Anatomie.d.une.chute.2023.FRENCH.1080p.BluRay.x264-UTT                        10.0 / 35
Anatomy.of.a.Fall.2023.1080p.BluRay.x264-JustWatch                             9.0 / 80
Parasite.2019.KOREAN.1080p.BluRay.x264.DTS-FGT                                12.0 / 150
Parasite.2019.1080p.BluRay.x264.DUBBED-GRP                                     8.0 / 20
Parasite.2019.VOSTFR.1080p.WEB.H264-GRP                                        4.0 / 15
```
- [ ] **Step 2: Failing tests**:
  - `test_classify_fixture` (parametrized over the fixture)
  - `test_matches_movie_accents_and_original_title`: "Anatomie.d.une.chute.2023…" matches MovieInfo(title "Anatomy of a Fall", original_title "Anatomie d'une chute", year 2023)
  - `test_matches_movie_rejects_wrong_year`: 2021 vs 2024
  - `test_language_rules`: en film rejects FRENCH and accepts MULTi; fr film accepts FRENCH and untagged; ko film rejects DUBBED and accepts KOREAN-tagged (original) and VOSTFR
- [ ] **Steps 3–5:** FAIL → implement → PASS. If guessit misreads a fixture name, fix it in the token layer, not by editing the expectation. **Commit** `feat: classify torrent release names`

---

### Task 8: Ranking: Recommended and More

**Files:**
- Create: `app/cinema/ranking.py`, `app/tests/test_ranking.py`

**Interfaces:**
- Consumes: `Classified`, `classify`, `matches_movie`, `language_ok` (Task 7); `MovieInfo` (Task 6).
- Produces:
  - `Group(resolution: int, standard: list[Classified], atmos: list[Classified])`
  - `size_floor_bytes(resolution: int, runtime_min: int | None) -> int`: `runtime × 0.045 GB` for 1080p, ×1.3 for 1440p, ×2 for 2160p, ×0.5 for 720p. Runtime defaults to 120. GB means 10⁹.
  - `score(c: Classified, movie: MovieInfo) -> float`, made of:
    - source: bluray 30, webdl 20, webrip 10, otherwise 0 (remux 0)
    - language: original 10, multi or french_subs 0
    - size: 15 inside the sweet spot, falling linearly to 0 at 0.5× the low end and 2× the high end. Sweet spots (scaled by runtime/120): 720p 4–8 GB, 1080p 8–16, 1440p 10–20, 2160p 15–35.
    - seeders: `min(15, 5 * log10(seeders + 1))`
  - `candidates(results: list[Classified], movie: MovieInfo) -> list[Classified]` (hard filters: `matches_movie`, `language_ok`, not cam, not 3D, resolution ≥ 720, seeders ≥ 1)
  - `recommended(results, movie) -> Classified | None`. It starts from `candidates`, then requires seeders ≥ 3, no remux, a group outside {`YTS`, `YIFY`} (case-insensitive, matched also against `[YTS.MX]`-style tokens), not `dv_only`, and `size >= size_floor_bytes`. Tier order: (1440, std), (1080, std), (2160, std), (1440, atmos), (1080, atmos), (2160, atmos). 720p never counts. The first non-empty tier wins, sorted by `score` desc, then seeders desc, then title asc.
  - `more_groups(results, movie) -> list[Group]`: `candidates` grouped in the order [2160, 1440, 1080, 720], non-empty groups only, each list sorted like `recommended`.
  - `speed_label(seeders: int) -> str`: `"fast"` at ≥ 50, `"ok"` at ≥ 10, otherwise `"slow"`.

- [ ] **Step 1: Failing tests** (built from the Task 7 fixture):
  - `test_recommended_prefers_1440_std`: Dune with all fixture releases → the 1440p WEB-DL
  - `test_recommended_falls_back_to_1080_bluray`: remove the 1440p → `SPARKS` (not FLUX Atmos, not QTZ MULTi, not YTS, not REMUX)
  - `test_recommended_atmos_only_when_nothing_else`: only Atmos releases → the 1080p FLUX
  - `test_recommended_rejects_below_size_floor`: a 1080p BluRay at 2 GB for a 166-min film → None when it's the only one
  - `test_recommended_none_when_no_candidates`
  - `test_more_groups_order_and_split`: resolutions `[2160, 1440, 1080]`; 1080 standard == {SPARKS, QTZ, YTS} with SPARKS first; 1080 atmos == {FLUX, FGT}; 2160 standard == {DV release}, 2160 atmos == {SWTYBLZ}; HDCAM and FRENCH-only absent everywhere
  - `test_speed_label`
- [ ] **Steps 2–4:** FAIL → implement → PASS. **Step 5: Commit** `feat: rank releases for Recommended and More`

---

### Task 9: Prowlarr client

**Files:**
- Create: `app/cinema/prowlarr.py`, `app/tests/test_prowlarr.py`

**Interfaces:**
- Consumes: `Release` (Task 7).
- Produces:
  - `class ProwlarrClient(base_url: str, api_key: str, http: httpx.AsyncClient)`, with:
    - `async search(queries: list[str]) -> list[Release]`: one `GET /api/v1/search` per query with params `query`, `type=movie`, `categories=2000`, `limit=100` and header `X-Api-Key`. Keep `protocol == "torrent"`. Map `guid`, `title`, `size`, `seeders` (None → 0), `indexer`, `downloadUrl`, `magnetUrl`, `infoHash`. Dedupe by lowercase `infoHash`, else by `guid`. Queries run concurrently.
    - `async resolve(r: Release) -> TorrentSource`: if `r.magnet` starts with `magnet:`, return it. Otherwise GET `download_url` with `follow_redirects=False`. A 3xx with a `magnet:` Location returns that string. A 3xx to http follows once. A 200 whose body starts with `b"d"` returns the bytes. Anything else raises `ProwlarrError`.
  - `ProwlarrError` on a missing api key (raised at call time), non-2xx, or a transport error.

- [ ] **Step 1: Failing tests**: `test_search_params_and_header`, `test_search_dedupes_by_infohash`, `test_search_drops_usenet`, `test_resolve_magnet_redirect`, `test_resolve_torrent_bytes`, `test_resolve_garbage_raises`, `test_missing_api_key_raises`.
- [ ] **Steps 2–4:** FAIL → implement → PASS. **Step 5: Commit** `feat: add Prowlarr search client`

---

### Task 10: Download lifecycle: poller, status, removal

**Files:**
- Modify: `app/cinema/downloads.py`, `app/cinema/main.py` (lifespan starts `run_poller` when `start_poller=True`)
- Create: `app/tests/test_downloads.py`

**Interfaces:**
- Consumes: `Store`, `Movie` (Task 3); `RqbitClient`, `TorrentStats` (Task 2); `READY_BYTES` (Task 4).
- Produces:
  - `STALL_SECONDS = 600`
  - `async refresh(store: Store, rqbit: RqbitClient, now: float) -> None`: for each `downloading` movie, fetch stats and update `progress_bytes`, `download_bps`, `eta_s`. Set `last_progress_at = now` whenever `progress_bytes` grew. When `finished`, call `rqbit.pause` and set status `done`. When `now - last_progress_at > STALL_SECONDS` and `live_peers == 0`, set `failed`. An `RqbitError` for one movie is logged and skipped.
  - `async run_poller(store, rqbit, interval: float = 3.0) -> None`: an endless loop that calls `refresh` with `time.time()`.
  - `async remove_movie(store: Store, rqbit: RqbitClient, movies_dir: Path, tmdb_id: int) -> None`: call `rqbit.delete` (ignore a 404 `RqbitError`), `shutil.rmtree` the movie's folder (the parent of `file_path`, only if it sits inside `movies_dir`), then `store.delete`.
  - `status_view(m: Movie) -> dict` returning `{"state": "preparing" | "ready" | "done" | "failed", "percent": int, "eta_s": int | None, "may_buffer": bool}`:
    - `preparing` while `progress_bytes < READY_BYTES`
    - `may_buffer = download_bps < size / (runtime_min*60)`, with runtime defaulting to 120 and always False when done

- [ ] **Step 1: Failing tests**:
  - `test_refresh_updates_progress`
  - `test_refresh_finished_pauses_and_marks_done`
  - `test_refresh_marks_stalled_failed` (no growth for 601 s with 0 live peers)
  - `test_refresh_not_failed_if_peers_present`
  - `test_remove_deletes_torrent_folder_and_row`
  - `test_remove_refuses_path_outside_movies_dir`
  - `test_status_view_states_and_may_buffer`
- [ ] **Steps 2–4:** FAIL → implement → PASS. **Step 5: Commit** `feat: track download progress and removal`

---

### Task 11: JSON API for the phone

**Files:**
- Create: `app/cinema/api.py`, `app/tests/test_api.py`
- Modify: `app/cinema/main.py` (mount under `/api`; `create_app` overrides `tmdb`, `prowlarr`)

**Interfaces:**
- Consumes: Tasks 3, 6, 8, 9, 10 (`start_download` from Task 5).
- Produces (all JSON):
  - `GET /api/home` → `{"library": [LibraryItem], "trending": [Card], "popular": [Card], "disk": {"free_bytes", "total_bytes"}}`. Disk usage comes from `shutil.disk_usage(movies_dir)`. If TMDB fails, `trending`/`popular` are `[]` and `"catalog_error": true` is set; the library still returns.
  - `GET /api/search?q=` → `{"results": [Card]}`
  - `GET /api/movies/{tmdb_id}` → `{"movie": MovieInfo-as-dict, "library": LibraryItem | null}`
  - `GET /api/movies/{tmdb_id}/releases` → `{"recommended": ReleaseView | null, "groups": [{"resolution", "standard": [ReleaseView], "atmos": [ReleaseView]}]}`. Queries: `"{title} {year}"`, plus `"{original_title} {year}"` when the original title differs. Results are cached per movie for 10 min, keyed by `release_id = sha1(guid)[:12]`.
  - `POST /api/movies/{tmdb_id}/download` with body `{"release_id": str}` → `201` LibraryItem. `409` if already in the library. `410` if the release id is unknown or expired (the client refetches).
  - `DELETE /api/movies/{tmdb_id}` → `204`
  - `Card = {"id", "title", "year", "poster_path", "in_library": bool}`
  - `LibraryItem = {"tmdb_id", "title", "year", "poster_path"} | status_view(m)`
  - `ReleaseView = {"id", "resolution", "size_bytes", "source": "bluray" | "web" | "other"` (remux and bluray → bluray; webdl and webrip → web), `"sound": "atmos" | channels | null, "lang": "original" | "multi" | "french_subs", "speed": speed_label(seeders)}`
  - Upstream failures (`TmdbError`, `ProwlarrError`, `RqbitError`) → `502 {"error": "upstream", "service": "tmdb" | "prowlarr" | "rqbit"}`.
  - Per-movie `asyncio.Lock` around download so concurrent POSTs serialize; the second one gets `409`.

- [ ] **Step 1: Failing tests** (fakes for TMDB via MockTransport, `FakeRqbit`, and a Prowlarr MockTransport serving the Task 7 fixture):
  - `test_home_shape`
  - `test_home_survives_tmdb_down`
  - `test_releases_recommended_and_groups`
  - `test_download_recommended_creates_library_item`
  - `test_download_twice_conflicts`: two concurrent POSTs → exactly one `201`, one `409`, one torrent in `FakeRqbit`
  - `test_download_expired_release_410`
  - `test_releases_upstream_error_is_502`
  - `test_delete_removes`
  - `test_release_view_never_exposes_names`: no key in any ReleaseView equals `title`/`guid`/`seeders`
- [ ] **Steps 2–4:** FAIL → implement → PASS. **Step 5: Commit** `feat: add phone JSON API`

---

### Task 12: Phone web app

**Files:**
- Create: `app/cinema/static/{index.html, app.js, i18n.js, styles.css, manifest.webmanifest, apple-touch-icon.png, icon-512.png}`
- Modify: `app/cinema/main.py` (serve `static/` at `/`, with `index.html` for `/`)

**Interfaces:**
- Consumes: the Task 11 endpoints. Images come from `https://image.tmdb.org/t/p/w342{poster_path}` and `/w1280{backdrop_path}`.

Screens (hash router): `#/` home, `#/search?q=`, `#/movie/{id}`.

Exact copy (`i18n.js`; `{n}`, `{m}` are placeholders):

| key | en | fr |
|---|---|---|
| recommended | Recommended | Recommandé |
| more | More | Plus |
| on_server | On the server | Sur le serveur |
| trending | Trending this week | Tendances de la semaine |
| popular | Popular | Populaires |
| search | Search movies | Rechercher un film |
| finding | Finding the best version… | Recherche de la meilleure version… |
| preparing | Getting ready… | Préparation… |
| ready | Ready to watch on TV | Prêt à regarder sur la TV |
| may_buffer | may pause to buffer | peut faire des pauses pour charger |
| downloading | Downloading {n}% · {m} min left | Téléchargement {n} % · {m} min restantes |
| done | On the TV | Sur la TV |
| failed | Couldn't download — try another version | Échec du téléchargement — essayez une autre version |
| none_found | No good version yet — try More | Pas encore de bonne version — essayez Plus |
| remove | Remove | Supprimer |
| remove_confirm | Remove this movie from the TV? | Supprimer ce film de la TV ? |
| standard | Standard | Standard |
| atmos | Dolby Atmos | Dolby Atmos |
| bluray | Blu-ray | Blu-ray |
| web | Streaming | Streaming |
| lang_original | Original | VO |
| lang_multi | Multi | Multi |
| lang_french_subs | French subs | VOSTFR |
| speed_fast / speed_ok / speed_slow | Fast / OK / Slow | Rapide / OK / Lent |
| get_it | Get this version | Prendre cette version |
| free_space | {n} GB free | {n} Go libres |
| error | Something went wrong — try again | Un problème est survenu — réessayez |

Behaviour:
- Recommended tap: show `finding`, fetch releases, POST the recommended id, then show the status. If `recommended` is null, show `none_found`.
- More: open a sheet listing groups, for example "1080p" with Standard and Dolby Atmos subsections. Each row shows size, source, sound, language, and speed. Tap → confirm (`get_it`) → POST.
- Status rows poll `/api/home` every 3 s while any item is preparing or ready. On `410`, refetch releases. On `502`, or any other error, show `error`.
- Design: dark, poster-first, tap targets ≥ 44 px, one column at phone width, no horizontal scroll. `manifest.webmanifest` uses `display: standalone` with name "Cinema". Include the `apple-touch-icon` meta (180×180 PNG; generate it with a stdlib zlib/struct script, drawn as a simple film-frame glyph).

- [ ] **Step 1: Implement the static files.** No unit tests: this is a UI.
- [ ] **Step 2: Local smoke test.** Run `TMDB_TOKEN=<real token> .venv/bin/uvicorn --factory cinema.main:build --port 8888` (with `DB_PATH`, `MOVIES_DIR` set to tmp). Open it at 390 px width in a browser:
  - home renders trending posters
  - search "dune" shows results
  - the movie page shows both buttons, in English and with French browser language

  Release search needs Prowlarr, so test it on the NAS in Task 14.
- [ ] **Step 3: Commit** `feat: add family phone web app`

---

### Task 13: setup.py v2

**Files:**
- Modify: `setup.py`, `tests/test_setup.py`

**Interfaces:**
- Produces (stdlib only):
  - `REQUIRED_KEYS = ["PUID", "PGID", "TZ", "DATA_PATH", "CONFIG_PATH", "TMDB_TOKEN"]`; `EnvConfig` exposes `tmdb_token` and an optional `v1_config_path` (default `None`) and drops `qb_password`.
  - `DATA_DIRS = ["movies"]`, `CONFIG_SERVICES = ["prowlarr", "rqbit/db", "rqbit/cache", "cinema"]`
  - `migrate_prowlarr_config(v1_config_path: str | None, config_path: str) -> bool`: copies `v1/prowlarr` into `config/prowlarr` (`shutil.copytree(dirs_exist_ok=True)`) only when the target has no `config.xml`. Returns whether it copied.
  - `StackLauncher.HEALTH_PORTS = {"prowlarr": 9696, "cinema": 8888}`; `start()` runs `sudo docker compose up -d --build`. `wait_for_config_files` waits only for the Prowlarr XML. `configure_auth` handles only prowlarr.
  - `plan_prowlarr_changes(proxies: list[dict], applications: list[dict]) -> tuple[bool, list[int]]`: whether to add the FlareSolverr proxy (no proxy with `implementation == "FlareSolverr"`), and the ids of applications whose `implementation == "Radarr"`.
  - `ProwlarrConfigurator(config_path)`; `configure()` applies the plan via the API. Creating the tag stays idempotent: reuse an existing `flaresolverr` tag.
  - Remove `QBittorrentConfigurator` and `RadarrConfigurator`.
  - `main()` prints the final URLs (`http://<NAS-IP>:8888` for phones, `:9696` for Prowlarr) and the three Infuse steps from the spec.

- [ ] **Step 1: Update the tests**:
  - Delete the qBittorrent/Radarr tests.
  - Update the EnvConfig fixtures to the new keys.
  - Add `test_migrate_copies_when_empty`, `test_migrate_skips_when_present`, `test_migrate_noop_without_v1`, `test_plan_adds_proxy_and_removes_radarr`, `test_plan_idempotent`.
- [ ] **Step 2: Run** `python3 -m unittest discover -s tests -q` → FAIL.
- [ ] **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `feat: rewrite setup.py for the v2 stack`

---

### Task 14: Migrate the NAS, README, end-to-end check

**Files:**
- Rewrite: `README.md`

- [ ] **Step 1: README** covering:
  - what it does
  - prerequisites: the TMDB token signup at themoviedb.org → Settings → API → "API Read Access Token", and Infuse Pro
  - quick start (`.env`, `python3 setup.py`) and `deploy.sh`
  - the family workflow in 4 lines
  - the Infuse setup steps
  - where files live, the backup (`/volume1/cinema/config`), and the legal note on uploads
- [ ] **Step 2: Commit** `docs: rewrite README for v2`
- [ ] **Step 3: Check v1 for unfinished downloads (ask the user before deleting anything).** Open qBittorrent (`:8080`) and list torrents; also list `/volume1/cinema/data/torrents/movies`. Report to the user and wait for their go-ahead.
- [ ] **Step 4: Stop v1.** On the NAS: `cd /volume1/cinema/Cinema-feat-cinema-stack && sudo docker compose down`. The config dirs stay as backup.
- [ ] **Step 5: Real `.env`.** Ask the user for their TMDB token and write it into `/volume1/cinema/Cinema/.env` over SSH, without echoing it into logs. Then run `./deploy.sh`, and on the NAS `python3 setup.py`.

  Expected: setup reports Prowlarr migrated and configured, and `http://192.168.2.191:8888` loads.
- [ ] **Step 6: End-to-end (with the user).** On a phone: search for a movie, tap Recommended, and see "Ready to watch on TV" within about 2 min. On the Apple TV, it appears in Infuse under Cinema → Movies with a poster and plays while downloading. Also check More shows groups with the Standard/Atmos split, and that Remove deletes the movie from the phone and from Infuse.
- [ ] **Step 7: Health.** `sudo docker stats --no-stream` (RAM under the 2 GB total), and `uploaded_bytes` ~0 in rqbit stats for the test movie.
- [ ] **Step 8:** Use superpowers:finishing-a-development-branch.
