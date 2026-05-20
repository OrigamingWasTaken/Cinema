# Cinema Stack — Home Movie Download & Playback Workflow

## Overview

A Docker-based media acquisition stack running on a Synology NAS. Users browse and request movies from their phone (iOS/Android); movies are automatically downloaded via torrents, renamed, organized, and made available to Infuse on Apple TV via SMB. Local network access only.

Legal context: downloading copyrighted content for personal use is legal in Switzerland.

## Architecture

```
Phone (LunaSea / Radarr Web UI)
        │
        ▼
    ┌─────────┐     searches      ┌──────────┐
    │  Radarr  │ ◄──────────────► │ Prowlarr │ ──► Torrent indexers
    │  :7878   │                  │  :9696   │
    └────┬─────┘                  └──────────┘
         │ sends torrent                │
         ▼                              ▼
    ┌──────────────┐            ┌──────────────┐
    │ qBittorrent  │            │ FlareSolverr │
    │    :8080     │            │    :8191     │
    └──────┬───────┘            └──────────────┘
           │ Radarr hardlinks & renames
           ▼
    /data/media/movies/
      Movie Name (2024)/
        Movie Name (2024).mkv
           │
           ▼
    Infuse (Apple TV) reads via SMB
```

### Services

| Service        | Image                                    | Port | Role                                      |
|----------------|------------------------------------------|------|-------------------------------------------|
| qBittorrent    | `linuxserver/qbittorrent`                | 8080 | Torrent download client                   |
| Radarr         | `linuxserver/radarr`                     | 7878 | Movie wishlist, search, rename, organize  |
| Prowlarr       | `linuxserver/prowlarr`                   | 9696 | Indexer manager, syncs to Radarr          |
| FlareSolverr   | `ghcr.io/flaresolverr/flaresolverr`     | 8191 | Cloudflare bypass proxy for indexers      |

### Networking

- All services on a single Docker bridge network (`cinema`)
- Services reference each other by container name (e.g., `http://qbittorrent:8080`)
- All ports bound to `127.0.0.1` only (no internet exposure)
- LAN access via Synology's IP (e.g., `http://192.168.1.x:7878`)

### Volume Strategy

A single shared data root avoids cross-filesystem copies and enables hardlinks:

```
/volume1/data/              ← mounted as /data in containers
├── torrents/
│   └── movies/             ← qBittorrent downloads here
└── media/
    └── movies/             ← Radarr hardlinks & renames here
```

Config volumes are per-service, stored relative to the compose file:

```
./config/
├── qbittorrent/
├── radarr/
├── prowlarr/
└── flaresolverr/
```

## Deliverables

### 1. `docker-compose.yml`

Defines all four services with:
- `linuxserver/*` images for qBittorrent, Radarr, Prowlarr
- `ghcr.io/flaresolverr/flaresolverr` for FlareSolverr
- Environment variables sourced from `.env` (PUID, PGID, TZ, data path)
- Shared `/data` volume mount for qBittorrent and Radarr
- Per-service config volume mounts
- `unless-stopped` restart policy
- Health checks where supported
- Bridge network `cinema`

### 2. `.env.example`

Template with:
- `PUID` / `PGID` — Synology user/group IDs (default: 1000)
- `TZ` — timezone (`Europe/Zurich`)
- `DATA_PATH` — Synology data root (`/volume1/data`)
- `CONFIG_PATH` — config root (`./config`)
- `QB_PASSWORD` — initial qBittorrent web UI password

### 3. `setup.py`

Python script (stdlib only, no pip dependencies) that automates the full setup:

**Phase 1 — Environment validation:**
- Check Docker and docker-compose are available
- Check `.env` exists (copy from `.env.example` if not, prompt user to edit)
- Validate DATA_PATH exists on the Synology filesystem

**Phase 2 — Directory creation:**
- Create `torrents/movies/` and `media/movies/` under DATA_PATH
- Create `config/` subdirectories for each service
- Set ownership to PUID:PGID

**Phase 3 — Stack launch:**
- Run `docker compose up -d`
- Wait for all containers to be healthy (poll health endpoints with backoff, timeout after 120s)

**Phase 4 — Service configuration via APIs:**

*qBittorrent:*
- Read the auto-generated temporary password from container logs (`docker logs qbittorrent`)
- Authenticate with `admin` + temporary password
- Set new password from `.env` QB_PASSWORD
- Set default save path to `/data/torrents/movies`
- Create "movies" download category
- Disable UPnP

*Radarr:*
- Read API key from `config/radarr/config.xml`
- Add qBittorrent as download client (host: `qbittorrent`, port: 8080, category: `movies`)
- Add root folder `/data/media/movies`
- Set naming scheme: `{Movie CleanTitle} ({Release Year})/{Movie CleanTitle} ({Release Year}) - {Quality Full}`
- Set quality profile to "HD-1080p" as default

*Prowlarr:*
- Read API key from `config/prowlarr/config.xml`
- Add FlareSolverr as proxy (`http://flaresolverr:8191`)
- Add Radarr as application (using Radarr's API key and internal URL)
- Add common public indexers: 1337x, TorrentGalaxy, LimeTorrents

**Phase 5 — Summary:**
- Print URLs for all services
- Print remaining manual steps
- Verify all services are responding

### 4. `README.md`

Contents:
- Prerequisites (Synology with Docker/Container Manager)
- Quick start (clone, copy `.env.example`, edit, run `setup.py`)
- Service URLs and default credentials
- Remaining manual steps:
  - Add additional/private indexers in Prowlarr
  - Configure Infuse on Apple TV (add SMB share)
  - Install LunaSea on iOS for mobile access (or use Radarr web UI on Android)
  - Optional: adjust quality profiles in Radarr
- Backup strategy (back up `./config/` directory)
- Troubleshooting common issues

## Mobile Workflow (Day-to-Day)

1. Open LunaSea (iOS) or Radarr web UI in browser (Android)
2. Search for a movie by title
3. Tap "Add" → select quality profile → confirm
4. Radarr searches configured indexers via Prowlarr
5. Best match sent to qBittorrent for download
6. On completion, Radarr hardlinks file to `/data/media/movies/Movie (Year)/`
7. Movie appears in Infuse on Apple TV

## Constraints & Decisions

- **No VPN**: Swiss law permits personal downloads; no VPN tunnel needed
- **Local access only**: No reverse proxy, no Tailscale, no port forwarding
- **No Plex/Jellyfin**: Infuse connects directly to SMB share, no media server needed
- **Hardlinks over copies**: Single `/data` mount ensures Radarr uses hardlinks, saving disk space and time
- **stdlib-only Python**: setup.py uses only Python standard library (urllib, xml, json, subprocess) — no pip install needed on the Synology
- **linuxserver images**: Consistent PUID/PGID handling, well-maintained, Synology-compatible
