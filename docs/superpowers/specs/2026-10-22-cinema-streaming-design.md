# Cinema v2 — Watch While Downloading

Supersedes `2026-05-20-cinema-stack-design.md`.

## Intent

Any family member picks up their phone, finds a movie in a poster catalog, taps one button, walks to the
Apple TV, and starts watching in Infuse within a minute or two — while the movie is still downloading.
The v1 stack (Radarr + qBittorrent + SMB) cannot do this: Radarr only imports finished downloads, and
Infuse over SMB stops playback at whatever was downloaded when play was pressed.

Legal context: downloading for private use is legal in Switzerland; *uploading* (seeding) is not.
The design keeps upload at the minimum the torrent engine allows.

## Goals

1. Phone web app (home-screen shortcut) with a Netflix-style catalog: search, trending, popular, posters.
2. On a movie page, two buttons:
   - **Recommended** — the system picks the release. No choices shown.
   - **More** — every acceptable release, grouped by resolution, each group split into *Standard* and
     *Dolby Atmos*.
3. Playback in Infuse on Apple TV while downloading, with seeking, without corrupt frames or stalls
   caused by missing data (it may buffer if the download is slower than the movie's bitrate).
4. Atmos available on request (via More), never forced.
5. Easy for non-technical family members: no logins, no torrent vocabulary, plain status words.

## Non-goals

- Access from outside the home network (later: Tailscale). LAN only.
- TV shows. Movies only.
- Accounts, per-person watchlists, parental controls.
- Transcoding. Infuse plays files as-is.
- Subtitle handling on the server — Infuse Pro fetches subtitles itself (OpenSubtitles).

## Anti-goals (failure even if it "works")

- A family member has to see a torrent name, a codec, or a seeder count to get a movie.
- Recommended picks a camera recording, a bitrate-starved encode, or a dubbed version.
- Playback shows garbage frames / crashes because Infuse read bytes that weren't downloaded yet.
- The NAS seeds content to the internet.

## Constraints (verified on the box, 2026-10-22)

- **NAS:** Synology DS720+ at `192.168.2.191`, Intel J4125 (x86_64), 2 GB RAM (~65% already used by
  Home Assistant, Homebridge and the v1 stack), DSM 7.2.1, Container Manager installed, SSH enabled.
- **Storage:** `/volume1` btrfs, ~1.2 TB free of 5.36 TB.
- **v1 deployment:** compose project in `/volume1/cinema/Cinema-feat-cinema-stack`, data in
  `/volume1/cinema/data` (`torrents/movies`, `media/movies`). Running: radarr, qbittorrent, prowlarr,
  flaresolverr.
- **Display:** 1080p projector, speakers without Atmos.
- **Apple TV app:** Infuse Pro (user agreed to buy). Free Infuse won't play Dolby/DTS audio.
- NAS credentials live in `creds.txt` (line 1 user, line 2 password) at the repo root, git-ignored, never
  printed or committed.

## Decided with the user

- Infuse on Apple TV, fed by a server that streams while downloading. (Stremio, Jellyfin, Plex rejected.)
- Recommended order: **1440p non-Atmos → 1080p non-Atmos → higher resolutions non-Atmos → Atmos only if
  it's the only kind available.** Quality must be genuinely good 1080p ("HD, not bad 1080p").
- Audio language = original version: English films in English, French films in French, Korean films in
  Korean, etc.
- More = categories per resolution, split Atmos vs not.

## Architecture

```
Phone browser ──HTTP──► cinema app (:8888) ──► TMDB API (catalog, posters, original language)
                         │      │
                         │      └──► Prowlarr (:9696) ──► indexers (FlareSolverr for Cloudflare)
                         │
                         ├──► rqbit HTTP API (:3030, internal only): add / status / stream / delete
                         │
Apple TV (Infuse) ◄──WebDAV /dav──┘  in-progress file → proxied rqbit stream (blocks until pieces arrive)
                                     finished file    → served straight from disk
```

### Containers (one compose project)

| Service | Image | Exposed | Role |
|---|---|---|---|
| `cinema` | built from `./app` | `8888` (LAN) | Phone web app, JSON API, WebDAV for Infuse |
| `rqbit` | `ikatson/rqbit:9.0.1` (pinned) | `6881` tcp/udp (peers); API internal only | Download + streaming engine |
| `prowlarr` | `linuxserver/prowlarr` | `9696` (LAN, admin) | Indexer search (config migrated from v1) |
| `flaresolverr` | `ghcr.io/flaresolverr/flaresolverr` | none | Cloudflare bypass for Prowlarr |

Radarr and qBittorrent are removed. Their config dirs stay on disk untouched as a backup.

### Why rqbit

- Downloads sequentially; streams any file over HTTP at `/torrents/{id}/stream/{file_idx}` with Range
  support, *blocks the response until the requested pieces arrive*, and prioritizes those pieces. Seeking
  ahead fetches that region first.
- HTTP API: `POST /torrents` (magnet / .torrent URL / bytes, with `list_only`, `only_files`,
  `output_folder`), `GET /torrents/{id}/stats/v1`, `POST /torrents/{id}/pause|delete`.
- Upload: `--ratelimit-upload` (bytes/s, min 1) and a `--disable-upload` flag that only exists if the
  image was built with that cargo feature. Builder must check which is available in the pinned image and
  use `--disable-upload` if present, else `RQBIT_RATELIMIT_UPLOAD=1`. Finished torrents are paused.

### Why WebDAV and not SMB / rqbit's UPnP

- SMB: Infuse snapshots the file size, partial files read as zeros or stop early.
- rqbit has a UPnP server, but Infuse's UPnP support is poorly documented and we couldn't control naming.
- WebDAV is officially supported by Infuse. We serve it ourselves, so we report the **final file size**
  from torrent metadata, present clean names Infuse can match to metadata
  (`/dav/Movies/Title (Year)/Title (Year).mkv`), and pass Range requests through to rqbit, which waits
  for missing pieces instead of returning garbage.

## The cinema app

Python 3.12, FastAPI + uvicorn, httpx (async client), guessit (release-name parsing), SQLite (stdlib
`sqlite3`, file in `/config/cinema.db`). Frontend: static vanilla HTML/CSS/JS modules served by FastAPI —
no Node build step, so the image builds quickly on the NAS. *(Builder's call within these lines.)*

### Catalog (TMDB)

- Needs a free TMDB API read-access token in `.env` (`TMDB_TOKEN`) — the one manual signup step.
- Home: search box, "On the server" row (movies downloading or downloaded, with status), Trending this
  week, Popular. Posters from TMDB image CDN.
- Movie page: backdrop, title, year, runtime, overview, and the two buttons (or status + Remove if the
  movie is already on the server).
- TMDB fields used downstream: `id`, `imdb_id`, `title`, `original_title`, `release_date`, `runtime`,
  `original_language`.

### Release search & classification

- Query Prowlarr `GET /api/v1/search` with `type=movie`, `categories=2000`, query `"{title} {year}"`;
  if the title is non-English, also query `original_title`. Merge, dedupe by infohash / guid.
- Parse each release name with guessit → resolution, source, other tags, audio codec/profile, languages,
  release group, 3D, edition. Keep a small hand-written tag layer for what guessit misses
  (`MULTi`, `VFF`, `VFQ`, `TRUEFRENCH`, `VOSTFR`, `DDP5.1.Atmos`, `TrueHD.Atmos`, `DV` without `HDR`).
- Drop releases whose parsed title/year don't match the movie (year ±1).

**Hard filters (apply to Recommended *and* More):**
- No CAM / TS / TC / SCR / R5 / workprint / HDCAM / 3D.
- Seeders ≥ 1 (More), ≥ 3 (Recommended).
- Language: from TMDB `original_language`:
  - `fr` → release must carry French audio (`FRENCH`, `TRUEFRENCH`, `VFF`, `VFQ`, `MULTi`).
  - anything else → must carry the original audio: reject French-only (`FRENCH`/`VFF`/`VFQ`/
    `TRUEFRENCH` without `MULTi`) and anything tagged `DUBBED`. Untagged releases count as original.
    `MULTi` and `VOSTFR` are allowed but ranked lower (Infuse often defaults to the French track of a
    MULTi file).

**Recommended — extra quality gate:**
- Size floor scaled by runtime: 1080p ≥ `runtime_min × 0.045 GB` (~5 Mbit/s), 1440p ×1.3, 2160p ×2.
  Fallback runtime 120 min if TMDB has none.
- Reject known micro-encode groups (`YTS`, `YIFY`) and `REMUX` (too large to stream-while-download
  comfortably).
- Dolby-Vision-only releases (no HDR10 fallback) rejected — the projector is SDR.

**Recommended — ordering:**
1. Tier by (resolution, Atmos): 1440p std → 1080p std → 2160p std → 1440p Atmos → 1080p Atmos →
   2160p Atmos. First non-empty tier wins.
2. Within the tier, score: source (BluRay encode > WEB-DL > WEBRip > HDTV), original-language-only over
   MULTi/VOSTFR, size closeness to a sweet spot (1080p ≈ 8–16 GB), then seeders (log-scaled).
   Exact weights are the builder's call; the ranking must be deterministic and covered by fixture tests
   built from realistic release names.
3. Nothing passes → Recommended button shows "No good version yet — try More".

**More — layout:** groups 2160p, 1440p, 1080p, 720p (only non-empty groups); inside each, *Standard*
then *Dolby Atmos*; rows sorted by the same score. Each row in plain words: size ("11 GB"), source
("Blu-ray" / "Streaming"), sound ("5.1" / "Atmos"), language badge ("Original" / "Multi" /
"French subs"), and speed as Fast / OK / Slow (from seeders). Tap → confirm sheet → download.

### Downloading

1. Resolve the release to something rqbit accepts: Prowlarr's download URL may return a `.torrent` file
   or redirect to a `magnet:` — the app fetches it and passes either the bytes or the magnet.
2. `POST /torrents?list_only=true` → pick the largest video file (`.mkv`, `.mp4`, `.m4v`, `.avi`),
   ignoring `sample`.
3. `POST /torrents` with `only_files=[idx]` and `output_folder=/data/movies/{Title (Year)}`.
4. Store in SQLite: tmdb id, title, year, release name, infohash, rqbit id, file idx, file name, final
   size, disk path, status, added_at.
5. Poll rqbit stats (every few seconds while anything is active). On finish → pause torrent, status
   `done`.

**Statuses shown to the family:** "Getting ready…" (no metadata / < threshold) → "Ready to watch on TV"
(first ~100 MB downloaded) with "may pause to buffer" if download speed < size/runtime → "Downloading
43% · 12 min left" (secondary line) → "On the TV" (done). Errors: "Couldn't download — try another
version" (stalled with no peers for 10 min, offers More).

**Removing:** movie page → Remove → confirm → rqbit delete (with files) + remove folder + DB row.
Free disk space shown at the bottom of the home screen.

### WebDAV (`/dav`, read-only)

- Methods: `OPTIONS` (`DAV: 1`), `PROPFIND` (Depth 0 and 1; Depth infinity treated as 1), `GET`,
  `HEAD`. Everything else → 405.
- Tree: `/dav/Movies/{Title (Year)}/{Title (Year)}.{ext}` for every DB movie, plus legacy files found
  under `/data/media/movies` (v1 Radarr imports), listed with their on-disk names.
- `getcontentlength` = final size from torrent metadata, even while downloading.
- `GET`/`HEAD` with `Range` → `206` + `Content-Range`, `Accept-Ranges: bytes`. Finished files streamed
  from disk; in-progress files proxied to rqbit's stream endpoint with the Range header passed through,
  body streamed, upstream request cancelled when Infuse disconnects.
- Optional HTTP Basic auth via `DAV_USER` / `DAV_PASSWORD` (off by default; LAN only).

## Deployment & setup

- New checkout at `/volume1/cinema/Cinema` (branch `streaming-redesign` until merged). Configs move to
  `/volume1/cinema/config/{prowlarr,rqbit,cinema}`; data stays `/volume1/cinema/data`, new downloads in
  `/volume1/cinema/data/movies`.
- `setup.py` stays stdlib-only and is rewritten for v2: validate `.env` (incl. `TMDB_TOKEN`), create
  dirs, copy v1 Prowlarr config if the new one is empty, `docker compose up -d --build`, wait for health,
  configure Prowlarr (FlareSolverr proxy if missing, remove the stale Radarr application), hand the
  Prowlarr API key to the app (env or `/config/cinema/secrets.json`), print URLs and the Infuse steps.
- Migration: stop & remove v1 `radarr` and `qbittorrent` containers (note any unfinished qBittorrent
  downloads first); v1 prowlarr/flaresolverr containers replaced by the v2 ones.
- Deploy from the dev PC over SSH (key-based; user installs the key once).

### Infuse setup (printed by setup.py, also in README)

1. Buy Infuse Pro (Family Sharing covers the household).
2. Add share → WebDAV → host `192.168.2.191`, port `8888`, path `/dav`, name "Cinema".
3. Settings → Subtitles: auto-download, English.

## Risks to retire first (spike before building)

1. **Infuse + WebDAV + rqbit blocking stream** on the real Apple TV: start playback of a legal torrent
   (Blender's *Sintel* / *Big Buck Bunny*) at < 5% downloaded, seek to 80%, confirm it buffers and then
   plays cleanly. Also check how Infuse behaves when a response blocks for 20+ s (timeout?).
2. Infuse library scan: does it read file bytes (thumbnails, media info) on scan, and does that trigger
   wasteful downloads? If so, delay listing an item until "Ready to watch".
3. Which upload-disable option the pinned rqbit image supports.
4. RAM headroom on the 2 GB NAS with v2 running.

## Left to the builder

Score weights, polling cadence, CSS/visual design (dark, poster-first, large tap targets), exact
thresholds marked "~", error copy, project layout inside `app/`, test structure.

## Calls made by Claude (user did not weigh in — check these)

- Python/FastAPI + vanilla JS (matches v1's Python; avoids a Node build on the NAS).
- LAN only, no logins.
- UI strings in English *and* French, chosen by phone language.
- Port 8888.
- Remux and YTS/YIFY excluded from Recommended; still visible in More (remux only).
- Default subtitle language English.
- Upload disabled/minimised and finished torrents paused (legal reasons above).
