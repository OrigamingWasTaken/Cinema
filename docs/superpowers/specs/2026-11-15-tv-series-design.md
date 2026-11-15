# TV series: Shows tab, seasons, follow, play while downloading

Full design. Agreed in conversation on 2026-11-15.

## Intent
The family already picks films on their phones and watches them in Infuse on the Apple TV while they download. They want the same for TV series, in two ways:
- **Whole seasons** of older or finished shows.
- **Shows still airing**, where new episodes arrive week by week.

Play-while-downloading is the top requirement ("playing while download is really important for me"). It must work for episodes exactly as it does for films: an episode shows up in Infuse within a couple of minutes and can be played before it finishes.

## Goals
- The tab bar becomes **Films · Shows · Search · Library · Downloads** (user's choice).
  - **Films** is today's Home: the hero, Trending this week and Popular.
  - **Shows** has a "Your shows" row (shows with anything on the server or followed), then popular and trending shows from TMDB.
  - **Library** has a Films / Shows switch at the top. Films is today's grid. Shows is a poster grid of shows with anything on the server; tapping one opens the show page. The switch is my call.
  - **Search** gains a **Shows** group (poster tiles, like Films). Exact name matches order first, like People and Studios do now.
  - **Downloads** lists episodes next to films, e.g. "Severance · S02E03", with the same states, progress and time left.
- **Show page** (`#/show/{tmdb_id}`):
  - A backdrop, title, years, overview and season list.
  - Each season row shows its state: not on the server → **Get season**; searching → "Looking for episodes…"; downloading → "Downloading 4 of 10 episodes"; done → "On the TV"; partly on the server → "7 of 10 on the TV" plus **Get the rest**.
  - Each season with anything on the server has **Remove** with a confirm. It deletes that season's files and stops monitoring it.
  - Tapping a season can expand its episode list (title, number, on the TV / downloading / not yet aired). Each episode is read-only: no per-episode download in v1.
- **One season at a time** (user's choice). Getting a season downloads every aired episode of that season, preferring a season pack.
- **Follow** appears only for shows that are still airing (TMDB `in_production` or status "Returning Series"). While followed, every new episode downloads by itself once it airs. Starting to follow also gets the current season's aired episodes, so nobody is left mid-season (my proposal, stated to the user). **Unfollow** stops new episodes and leaves existing files alone.
- **Infuse:**
  - The same WebDAV share (`/dav`) gets a `Shows` folder next to `Movies`. Infuse needs no new share.
  - The layout is `Shows/<Show Title (Year)>/Season NN/<Show Title> - SNNENN.<ext>`, which Infuse's TV scraper recognises.
  - **An episode's path never changes** between downloading and finished, so a paused or bookmarked episode keeps working.
  - An episode is listed once about 100 MB of it is downloaded (the films rule, `READY_BYTES`) and is streamed from rqbit until finished, then from disk.
- **Version choice is automatic**: there is no "More versions" for shows. Rules:
  - 1080p preferred, 720p accepted.
  - Original audio. No French-only dubs unless the show's original language is French, mirroring `releases.language_ok` for films.
  - No upgrades after a file exists: a file must never be swapped while someone watches it.
- **Anime** works through Nyaa, with anime episode numbering handled by Sonarr's anime series type. It is set automatically when TMDB says the show is Japanese animation (genre Animation, origin JP).
- **Indexers:** add **showRSS, LimeTorrents, TorrentDownload and Nyaa.si** to Prowlarr (tested working from the NAS on 2026-11-15) next to the existing TPB, Knaben and YTS. EZTV and 1337x time out behind Cloudflare, and TheRARBG and TorrentGalaxy are blocked by Prowlarr, so skip all four.
- The family never sees release names, codecs, seeders or anything about Sonarr.

## Non-goals
- Downloading single episodes on demand, or picking a version by hand.
- "Next episode" or watched tracking. Infuse does this.
- Auto-deleting watched episodes or a disk quota.
- Calendars or "airs tonight" notifications.
- Subtitles (Infuse fetches them).
- Exposing Sonarr's own web UI to the family. It stays reachable on the LAN for the admin only.

## Anti-goals (a failure even if it works)
- An episode can't be played until its whole season finishes.
- An episode's Infuse path changes when its download finishes, or a file is replaced mid-watch.
- Show downloads appear as films, or clutter the `Movies` folder in Infuse.
- Torrents keep seeding after they finish. The legal note in the README must stay true: upload is capped at ~1 B/s and finished torrents are paused or removed.
- A season takes twice its size on disk (download copy plus imported copy). Imports must hardlink or move, never copy.
- Adding Sonarr makes the NAS run out of memory and slows the running cinema/rqbit.

## Constraints
- **NAS:** DS720+, DSM 7.2.1, 1.75 GB RAM with ~620 MB available today. Sonarr (.NET) must fit in roughly 250 MB, so set the .NET GC to conservative/limited if needed. The disk has 1.2 TB free, and a 1080p season is ~10–30 GB.
- **Sonarr v5** is required: its native RQBit download client exists only there. It is a pre-release, so pin the exact image `lscr.io/linuxserver/sonarr:develop-5.0.0.2941-ls209` (published 2026-11-14) and never use a floating tag.
- **What Sonarr's RQBit client can and can't do** (read from Sonarr `develop`, `src/NzbDrone.Core/Download/Clients/RQBit/`):
  - Its settings are host, port (3030), SSL and URL base. It has **no category, label or save-path setting**.
  - It adds torrents with `POST /torrents?overwrite=true`, so they land in **rqbit's default output folder**.
  - It reports `torrent.Path` as the output path and `finished` as Completed.
  - It removes torrents via `/torrents/{hash}/forget` or `/delete`.
- **rqbit 9.0.1:**
  - Its default output folder today is `/data/movies` (`server start /data/movies`).
  - Our app always passes an explicit `output_folder` for films, so the default can change safely. Change it to a dedicated folder, e.g. `/data/downloads`, so Sonarr's torrents never sit among films. The builder verifies that existing film torrents keep their stored folders after the change.
  - rqbit's API (3030) must be reachable from the Sonarr container on the compose network. Today it is published only on `127.0.0.1:3030` on the host, while the cinema container reaches it by service name.
- **Paths:**
  - Sonarr mounts the same `${DATA_PATH}:/data` as rqbit, so download paths match with no remote path mapping.
  - The series root folder is `/data/shows`.
  - Hardlinks need the download and the library on the same filesystem and the same mount, which this setup gives.
- **Setup** (`setup.py`, run with sudo on the NAS) must configure Sonarr end to end, with no clicking in Sonarr's UI:
  - read Sonarr's API key from its config.xml;
  - set the root folder, the quality profile (rules above, upgrades off) and the RQBit download client;
  - turn on completed download handling with hardlinks;
  - register Sonarr as an application in Prowlarr so the TV indexers sync;
  - add the four new indexers.
  It must be idempotent, like the existing Prowlarr steps.
- **Existing behaviour stays:** the film flow, the WebDAV `Movies` tree, the API shapes the phone uses today, error copy, en/fr strings, and "ask before deploying" (a cinema restart interrupts Infuse).
- **Secrets:** Sonarr's API key is read inside scripts or the container, never printed or committed, the same as the Prowlarr key and the TMDB token.

## Approaches (chosen earlier)
- **A: our own TV logic on top of rqbit.** We'd write episode parsing, season pack handling, airing schedules and RSS polling ourselves. That's a lot of fragile code for something Sonarr already does well.
- **B: Sonarr with a different torrent client (qBittorrent, as v1 did).** It's proven, but qBittorrent doesn't stream while downloading. We'd lose play-while-downloading, the top requirement.
- **B′ (chosen by the user):** **Sonarr v5 drives our existing rqbit** through its native RQBit client.
  - Sonarr owns finding, grabbing and importing episodes, and following airing shows.
  - Our app owns the phone UI (Sonarr is invisible) and the WebDAV view.
  - The WebDAV maps Sonarr's queue to rqbit torrents, so in-progress episodes stream straight from rqbit like films do.
  - The cost is depending on a pre-release Sonarr, so it is pinned.

## Design
**Data flow:**
```
phone ── /api/shows/... ──► cinema ──► Sonarr API (add series, monitor season, search, unmonitor, delete files)
                               │            │
                               │            └─► Prowlarr indexers ─► RQBit client ─► rqbit (/data/downloads/…)
                               │                                                   └─ on finish: Sonarr imports (hardlink) ─► /data/shows/…
WebDAV /dav/Shows ◄────────────┘ reads Sonarr queue + episode files, streams in-progress files from rqbit, finished ones from disk
```

**Backend (new module, e.g. `app/cinema/sonarr.py` plus a `shows` part of the API):**
- **A Sonarr client** (httpx, API key header, cached reads). It calls Sonarr's documented API. The repo has both `Sonarr.Api.V3` and `Sonarr.Api.V5`; v3 is the stable one, and the builder picks per endpoint and records why. It covers:
  - series lookup by tvdb id;
  - add a series (unmonitored, no search);
  - monitor or unmonitor a season;
  - trigger a season search;
  - set the series' new-episode monitoring for Follow;
  - list episodes and episode files;
  - list the queue with episode details;
  - delete episode files.
- **Show metadata from TMDB** (existing `TmdbClient`): `/search/tv`, `/tv/{id}`, `/tv/{id}/season/{n}`, `/tv/{id}/external_ids` (for `tvdb_id`), and trending and popular TV. A show with no `tvdb_id` can't be added, so its page shows a friendly "not available" line instead of the buttons.
- **Phone API** (new GET/POST endpoints, same error conventions as films: 502 with `{"error":"upstream","service":"tmdb"|"sonarr"}`, 404 for an unknown id):
  - a show page with per-season and per-episode state merged from TMDB and Sonarr;
  - Get season;
  - Follow / Unfollow;
  - Remove season;
  - the Shows tab rows;
  - search including shows;
  - `/api/home` (or a sibling endpoint) gains episode downloads for the Downloads tab.
- **Episode download state**, merged from:
  - Sonarr's queue: download id = rqbit info hash, the episode ids, and the tracked state;
  - rqbit's torrent list and stats: info hash → torrent id, files, progress;
  - Sonarr's episode files, for finished ones.

  Map files to episodes inside a season pack by parsing file names (guessit is already a dependency) against the queue's episode list. A file with no confident match is not listed.
- **The finished-torrent rule:** once Sonarr has imported a torrent's episodes, that torrent must stop uploading: paused, or removed with its download copy deleted (the hardlinked library file stays). The builder chooses, and both satisfy the anti-goal.
- **Failures:**
  - Sonarr finds nothing for a season: "Looking for episodes…" for up to 24 h, then "Couldn't find this season yet" with **Try again**, which re-runs the season search.
  - A stalled torrent: the same 10-minute no-peers rule as films, shown as "Couldn't download", with Sonarr told to blocklist that release and search again.
- **Sonarr unreachable:** the Films tab and film flows keep working, and show screens show the error copy.

**WebDAV (`app/cinema/webdav.py`):**
- Add the `Shows` collection next to `Movies`, read-only, with the same PROPFIND/GET/HEAD and auth rules.
- An in-progress episode is proxied to `rqbit.stream_url(torrent_id, file_index)` with the same Range handling and client-disconnect logic as films.
- A finished episode is served from its Sonarr library path.
- The folder and file names come from the virtual layout above, not from the files on disk. The name is fixed from the series title (with year) and SxxEyy, so it is identical before and after import.

**Docker / setup:**
- A new `sonarr` service: pinned image, PUID/PGID/TZ, `${CONFIG_PATH}/sonarr:/config`, `${DATA_PATH}:/data`, port 8989 on the LAN, on the `cinema` network.
- Pass `SONARR_API_KEY` to cinema the same way as `PROWLARR_API_KEY`: read from the Sonarr config by setup, or a read-only mount of its config.
- Change rqbit's default output folder (see Constraints).
- `setup.py` gains the Sonarr steps listed under Constraints.
- The README gets a "Shows" section covering the Infuse folder, the Shows tab, Follow, and the pinned Sonarr version with how to bump it.

**Phone app (Svelte):**
- The new tabs and routes: `#/shows`, `#/show/{id}`, and Library's Films / Shows switch. Keep scroll memory, active-tab-to-top and hash routing as they work today.
- New en/fr copy:
  - Shows / Séries
  - Get season / Télécharger la saison
  - Follow / Suivre, Following / Suivie, Unfollow / Ne plus suivre
  - Season N / Saison N
  - "Downloading 4 of 10 episodes" / "4 épisodes sur 10 en téléchargement"
  - Looking for episodes… / Recherche des épisodes…
  - "Couldn't find this season yet" / « Saison introuvable pour l'instant »
  - Your shows / Vos séries
  - Not available / Non disponible

  The builder refines the wording and keeps the tone of the existing strings.

## Left to the builder
- Sonarr API version per endpoint (v3 vs v5), its polling interval and caching.
- How season and episode state is stored or derived: from Sonarr on each request with a short cache, or mirrored in SQLite.
- Pausing versus removing finished show torrents (see above).
- How the default rqbit folder change is migrated or verified.
- The exact quality profile and custom-format setup that implements the version rules.
- How the .NET memory cap is set.
- The visual details of the show page, season rows, episode list and Library switch, following the existing Svelte look.
- Test strategy: fake Sonarr like the existing fakes, plus a smoke test and screenshots as for the Svelte redesign.
