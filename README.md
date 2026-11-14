# Cinema

A home movie setup for a Synology NAS and an Apple TV. Anyone in the family
opens a web app on their phone, picks a movie, and a couple of minutes later it
is in Infuse on the TV, playable while it is still downloading.

```
phone web app (cinema :8888)  ->  rqbit downloads  ->  Infuse plays over WebDAV
                 \-> Prowlarr + FlareSolverr find the torrents
```

| Service | Port | Purpose |
|---------|------|---------|
| cinema | 8888 | Phone web app, TMDB metadata, WebDAV server at `/dav` |
| rqbit | 4240 | Torrent client (streams while downloading) |
| Prowlarr | 9696 | Indexer manager (search) |
| FlareSolverr | internal | Cloudflare helper for Prowlarr |

## Prerequisites

- Synology NAS with Docker (Container Manager) and SSH access. DSM's Python 3.8
  is fine.
- A TMDB token: themoviedb.org -> Settings -> API -> copy the long
  **API Read Access Token** (starts with `eyJ`), not the short API key.
- Infuse Pro on the Apple TV (Family Sharing covers the household).

## Setup (on the NAS)

```bash
cp .env.example .env
nano .env                  # fill in TMDB_TOKEN, PUID, PGID, paths
sudo python3 setup.py
```

- `PUID`/`PGID`: run `id` on the NAS and use your user and group IDs (for
  example `1024` and `100`).
- `sudo` is required: docker needs it, and a v1 Prowlarr config is only
  readable by root.
- `DAV_USER`/`DAV_PASSWORD` are optional. Leave them empty for no WebDAV login.

Setup creates the folders, starts the containers, migrates a v1 Prowlarr
config if one exists, and adds indexers. At the end it prints the phone app URL
(`http://192.168.x.x:8888`) and the Infuse settings.

### Indexers

When Prowlarr has none, setup adds The Pirate Bay, YTS and Knaben. A v1
migration keeps the indexers you already had. 1337x does not work: FlareSolverr
cannot pass its Cloudflare check.

## Deploying from a dev PC

```bash
./deploy.sh              # all services
./deploy.sh cinema       # only some services
```

This copies the working tree to the NAS over ssh and rebuilds the containers.
It needs:

- `creds.txt` next to the script: line 1 is the NAS user, line 2 is the sudo
  password. It is git-ignored and must never be committed.
- ssh key login: `ssh-copy-id user@NAS`. This fails unless DSM -> Control Panel
  -> User & Group -> Advanced -> **Enable user home service** is on.

## Using it (family)

1. Open the Cinema web app on the phone (`http://192.168.x.x:8888`) and add it
   to the home screen.
2. Search for a movie.
3. Tap **Recommended**, or **More** to pick a specific version.
4. Within about 2 minutes it shows up in Infuse under Cinema -> Movies. You can
   press play while it is still downloading.

## Infuse setup (Apple TV)

1. Add Files -> Add Share -> choose **WebDAV**, not SMB (an easy mistake).
2. Address: the NAS IP. Port: `8888`. Path: `/dav`.
3. Username and password: leave empty unless you set `DAV_USER`.
4. Optional: Settings -> Subtitles -> auto-download, English.

## Where things live

| What | Where |
|------|-------|
| New downloads | `${DATA_PATH}/movies/<Title (Year)>/` |
| v1 Radarr imports | `${DATA_PATH}/media/movies` (still visible in Infuse) |
| Service configs | `${CONFIG_PATH}` (`/volume1/cinema/config`) |

Back up `${CONFIG_PATH}`: it holds the Prowlarr indexers and API keys, the
cinema state and the rqbit database.

## Troubleshooting

- A far seek waits about 20 s while that part of the file downloads.
- "Couldn't download" means no peers for 10 minutes. Pick another version
  under **More**.
- The Infuse share does not work: check that it is WebDAV, not SMB, with port
  8888 and path `/dav`.
- Memory is small: cinema uses about 55 MiB and rqbit about 20 MiB.
  `sudo docker stats --no-stream` shows the current numbers.

## Legal note

rqbit's upload is capped at about 1 B/s (`RQBIT_RATELIMIT_UPLOAD=1`) and
finished torrents are paused, so the stack downloads but does not seed. You are
responsible for complying with copyright law where you live.
