# Cinema Stack

Movie download and playback workflow for Synology NAS + Apple TV (Infuse).

Browse movies on your phone, add them to your wishlist, and they automatically download, rename, and appear in Infuse.

## Prerequisites

- Synology NAS with Docker (Container Manager) installed
- SSH access to your Synology
- Python 3 (pre-installed on most Synology models)

## Quick Start

```bash
ssh your-synology
git clone <repo-url> Cinema
cd Cinema
cp .env.example .env
nano .env              # edit PUID, PGID, DATA_PATH, QB_PASSWORD
python3 setup.py
```

The setup script will:
1. Create the directory structure on your NAS
2. Start all Docker containers
3. Configure qBittorrent, Radarr, and Prowlarr automatically

## Services

| Service | URL | Purpose |
|---------|-----|---------|
| Radarr | `http://<NAS-IP>:7878` | Movie wishlist & manager |
| Prowlarr | `http://<NAS-IP>:9696` | Torrent indexer manager |
| qBittorrent | `http://<NAS-IP>:8080` | Torrent client |
| FlareSolverr | `http://<NAS-IP>:8191` | Cloudflare bypass |

## After Setup

1. **Add indexers** — Open Prowlarr (`:9696`) and add torrent indexers (e.g., 1337x)
2. **Configure Infuse** — On Apple TV, add an SMB share pointing to your movies folder
3. **Install LunaSea** (iOS) — Add your Radarr server for mobile browsing, or use the Radarr web UI in any browser

## Daily Workflow

1. Open LunaSea or Radarr web UI on your phone
2. Search for a movie → Add to wishlist
3. Movie downloads automatically and appears in Infuse

## Backup

Back up the `./config/` directory to preserve all service settings.

## Folder Structure

```
<DATA_PATH>/
├── torrents/movies/    # qBittorrent downloads
└── media/movies/       # Radarr organizes here → Infuse reads this
```
