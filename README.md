# Cinema

A home movie and TV setup for a Synology NAS and an Apple TV. Anyone in the
family opens a web app on their phone, picks a movie or a show season, and a
couple of minutes later it is in Infuse on the TV, playable while it is still
downloading.

```
phone web app (cinema :8888)  ->  rqbit downloads  ->  Infuse plays over WebDAV
                 \-> Prowlarr + FlareSolverr find the torrents
                 \-> Sonarr tracks shows and imports finished episodes
```

| Service | Port | Purpose |
|---------|------|---------|
| cinema | 8888 | Phone web app, TMDB metadata, WebDAV server at `/dav` |
| rqbit | 4240 | Torrent client (streams while downloading) |
| Prowlarr | 9696 | Indexer manager (search) |
| Sonarr | 8989 | Show manager (seasons, imports); admin only |
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
- `DAV_USER`/`DAV_PASSWORD` are optional. The WebDAV login is only turned on
  when both are set; leave both empty for no login. Setup warns if only one is
  set.

Setup creates the folders, starts the containers, migrates a v1 Prowlarr
config if one exists, and adds indexers. At the end it prints the phone app URL
(`http://192.168.x.x:8888`) and the Infuse settings.

### Migrating from v1

v1 and v2 both use containers named `prowlarr` and `flaresolverr` and port
9696, so stop v1 before the first v2 setup. Its configs stay where they are, as
a backup.

```bash
cd <v1 dir> && sudo docker compose down    # the folder with the v1 docker-compose.yml
cd <v2 dir> && sudo python3 setup.py
```

Set `V1_CONFIG_PATH` in `.env` to the v1 config folder so setup copies the v1
Prowlarr config. Setup stops with this same instruction if v1's Prowlarr is
still running.

### Indexers

When Prowlarr has none, setup adds The Pirate Bay, YTS and Knaben. A v1
migration keeps the indexers you already had. 1337x does not work: FlareSolverr
cannot pass its Cloudflare check.

## Shows

The **Shows** tab in the phone app works like the movies, one season at a time.

- **Get season** asks Sonarr to find and download that season. The episodes
  show up in Infuse as they download.
- **Follow** keeps an airing show: new episodes are downloaded as they air.
- **Remove** takes a requested season off the TV again.
- In Infuse, shows are in the `Shows` folder of the same Cinema share, next to
  Movies. A path stays the same before and after import.

Show indexers are showRSS, LimeTorrents, TorrentDownload and Nyaa.si (added by
setup). EZTV and 1337x are not used: EZTV is unreliable, and 1337x needs a
Cloudflare check FlareSolverr cannot pass.

Sonarr is pinned to `lscr.io/linuxserver/sonarr:develop-5.0.0.2941-ls209`
because the rqbit download client only exists in its develop builds. To bump it,
change the tag in `docker-compose.yml`, then `./deploy.sh` and re-run
`sudo python3 setup.py`. Sonarr's own web UI (port 8989) is for the admin only;
the family never needs it.

Setup also checks that Sonarr can hardlink from `downloads/` to `shows/`. If it
cannot, setup says so and exits with an error after the stack is up.

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

## Short address: http://cinema.local

- The `mdns` service publishes `cinema.local` for the NAS through DSM's own
  avahi (Bonjour). Override the name with `CINEMA_HOSTNAME`, or the IP with
  `NAS_IP`, in `.env`.
- To drop the `:8888`, add a DSM reverse proxy rule: Control Panel -> Login
  Portal -> Advanced -> Reverse Proxy -> Create. Source: HTTP, hostname
  `cinema.local`, port 80. Destination: HTTP, `localhost`, port 8888. Under
  Advanced settings, set the read/send timeouts to 300 s (a download request
  can take a few minutes).
- Infuse keeps using port 8888 directly (WebDAV); the short address is for phones.

## Using it (family)

1. Open the Cinema web app on the phone (`http://192.168.x.x:8888`) and add it
   to the home screen.
2. Find a movie on **Home** or under **Search**.
3. Tap **Recommended**, or **More versions** to pick a specific version.
4. Within about 2 minutes it shows up in Infuse under Cinema -> Movies. You can
   press play while it is still downloading. **Downloads** shows the progress;
   **Library** lists the films that are on the TV.

## Infuse setup (Apple TV)

1. Add Files -> Add Share -> choose **WebDAV**, not SMB (an easy mistake).
2. Address: the NAS IP. Port: `8888`. Path: `/dav`.
3. Username and password: leave empty unless you set both `DAV_USER` and
   `DAV_PASSWORD`.
4. Optional: Settings -> Subtitles -> auto-download, English.

## Where things live

| What | Where |
|------|-------|
| New movie downloads | `${DATA_PATH}/movies/<Title (Year)>/` |
| Show downloads (temporary) | `${DATA_PATH}/downloads/` |
| Shows (Sonarr imports, hardlinked) | `${DATA_PATH}/shows/<Show (Year)>/Season NN/` |
| v1 Radarr imports | `${DATA_PATH}/media/movies` (still visible in Infuse) |
| Service configs | `${CONFIG_PATH}` (`/volume1/cinema/config`) |

Back up `${CONFIG_PATH}`: it holds the Prowlarr indexers and API keys, the
cinema state, the Sonarr database and the rqbit database.

## Troubleshooting

- A far seek waits about 20 s while that part of the file downloads.
- "Couldn't download" means no peers for 10 minutes. Pick another version
  under **More versions**.
- The Infuse share does not work: check that it is WebDAV, not SMB, with port
  8888 and path `/dav`.
- Memory is small: cinema uses about 55 MiB, rqbit about 20 MiB and Sonarr about 250 MiB.
  `sudo docker stats --no-stream` shows the current numbers.

## Phone app development

The phone app is Svelte 5 + Vite in `app/web/`. The Docker image builds it; the
build output (`app/cinema/static/`) is not committed.

```bash
cd app/web
npm ci
npm run dev       # http://localhost:5173, proxies /api to a backend on :8888
npm run build     # writes app/cinema/static/ for the FastAPI app to serve
npm run check     # type-check
```

The backend tests do not need Node: `cd app && .venv/bin/python -m pytest -q`.

## Legal note

rqbit's upload is capped at about 1 B/s (`RQBIT_RATELIMIT_UPLOAD=1`) and
finished torrents are paused, so the stack downloads but does not seed. You are
responsible for complying with copyright law where you live.
