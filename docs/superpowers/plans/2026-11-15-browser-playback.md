# Season Labels and Browser Playback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** label show seasons with their name, year and next air date. Then let the family play finished films and episodes in the browser, at home and over Tailscale, at original quality.

**Architecture:**
- **Season labels** reuse the TMDB `/tv/{id}` response, behind a season-count guard.
- **Playback** adds a small media layer to the cinema app:
  - `media.py`: ffprobe metadata plus a keyframe index, cached on disk.
  - `hls.py`: keyframe-aligned VOD playlists, and capped, idle-killed ffmpeg sessions that copy the video and convert the audio to AAC stereo.
  - `play.py`: API routes. These include a direct byte-range file route for MP4s the browser can play as-is.
- **Phone app:** a lazy-loaded player page that uses native HLS, or hls.js when the browser lacks it.
- **Tailscale:** a pinned userspace-networking container, with setup printing the login link and the access policy.

**Tech Stack:** Python 3.12, FastAPI, ffmpeg/ffprobe (Debian package in the cinema image), Svelte 5 + Vite, hls.js 1.7.4, Tailscale v1.104.7, Docker Compose, setup.py on DSM's Python 3.8 stdlib.

**Spec:** `docs/superpowers/specs/2026-11-15-season-labels-design.md` (Task 1) and `docs/superpowers/specs/2026-11-15-browser-playback-design.md` (Tasks 2–6).

## Global Constraints
- **Season numbering** stays Sonarr's (TVDB). TMDB labels are shown only when TMDB and Sonarr have the same number of non-special seasons.
- **Video is never re-encoded.** Audio goes to AAC stereo at 192 kb/s. Segments are fMP4, about 6 s, cut on real keyframes.
- **Direct play** applies only to `.mp4`/`.m4v` files the browser reports it can play. MKV always goes through HLS. Direct streams don't count towards the cap.
- **At most 2 HLS sessions** run at once. A third gets 429 `{"error":"busy"}`. A new session from the same device for the same item replaces the old one.
  - A session idle for 60 s (no segment request) is killed and its temp dir deleted.
  - All sessions are killed on app shutdown.
  - ffmpeg runs under `nice -n 10`.
- **Only known finished files play:**
  - a Store film with status `done` and its `file_path` existing;
  - a tracker entry with state `done` and `path` set.
  - Anything else gives 404 `{"error":"not_found"}`. No path from the request is ever used as a filesystem path.
- **Errors:**
  - ffmpeg/ffprobe failure gives 502 `{"error":"upstream","service":"player"}`;
  - TMDB and Sonarr errors keep their existing mapping.
- **Phone app dependencies:** svelte, plus exactly one new runtime dependency, `hls.js@1.7.4`. It is loaded only with dynamic `import()` on the player page, and only when `video.canPlayType('application/vnd.apple.mpegurl')` is empty.
- **Copy is en and fr.** Never use `{@html}` with API strings.
- **Tailscale:**
  - image `tailscale/tailscale:v1.104.7` (pinned), hostname `cinema`, `TS_USERSPACE=true` (DSM 7.2 has no `/dev/net/tun`, checked 2026-11-15), state in `${CONFIG_PATH}/tailscale`;
  - Funnel is never enabled, and no port forwarding or public exposure is added.
- **Addresses:** at home `cinema.local`; away, the Tailscale name. Resume is per address, which is accepted.
- **Secrets:** never print, log or commit them (`TS_AUTHKEY`, API keys, TMDB token).
- **Commits:** single-line titles, no body, no attribution.
- **Tests:**
  - backend: `cd app && .venv/bin/python -m pytest -q`;
  - setup: `cd /home/marlon/Projects/Cinema && python3 -m unittest discover -s tests -q`;
  - web: `cd app/web && npm run check && npm run build`.
  - Real-ffmpeg tests are skipped when `shutil.which("ffmpeg")` is None.
- **Never touch the NAS or run `deploy.sh`.**

## Review Focus
1. **Seeking far ahead.** A request for a segment far past what ffmpeg is writing restarts ffmpeg at that segment. The returned segment's first frame time matches the playlist (±0.1 s), with no duplicate or missing segment. Tests: `test_seek_restarts_at_segment` (Task 3) and `test_real_ffmpeg_segment_times_match_playlist` (Task 3).
2. **Viewer closes the tab.** No more requests arrive, so ffmpeg is killed after 60 s idle and its temp dir is gone. A third viewer gets 429 until then. Tests: `test_idle_session_killed_and_cleaned` and `test_third_session_busy` (Task 3).
3. **Path safety.** Unknown ids, a `downloading` film, a tracker entry not `done`, and odd episode numbers all give 404. No request value reaches `open()`. Test: `test_play_unknown_or_unfinished_is_404` (Task 4).
4. **A browser that can't play HEVC.** The player shows the can't-play line, not a black screen. Covered by a smoke scene with `canPlayType` stubbed to `""` (Task 5).
5. **A TMDB season count that differs from Sonarr's.** The season rows show no TMDB names, so a season never gets the wrong name. Test: `test_page_labels_hidden_when_season_counts_differ` (Task 1).

---

### Task 1: Season names, years and "new episode" line

**Files:**
- Modify: `app/cinema/tmdb.py` (ShowInfo), `app/cinema/shows_service.py` (`page`), `app/web/src/lib/api.ts` (SeasonView), `app/web/src/components/SeasonRow.svelte`, `app/web/src/lib/i18n.ts`
- Test: `app/tests/test_tmdb.py`, `app/tests/test_shows_service.py`

**Interfaces:**
- Produces:
  - `ShowInfo.seasons: tuple[tuple[int, str, int | None], ...]` as `(number, name, air_year)`, built from TMDB `seasons[]` and excluding season 0.
  - `ShowInfo.next_episode: tuple[int, int, str] | None` as `(season, episode, air_date "YYYY-MM-DD")`, from `next_episode_to_air`.
  - Both default to empty / None, so existing constructions keep working.
- Each season dict from `ShowsService.page` gains:
  - `"label": str | None`: the TMDB name, or None when the name matches `^(Season|Saison) \d+$` (case-insensitive) or the guard fails;
  - `"year": int | None`;
  - `"next_air_date": str | None`.
- **Guard:** TMDB labels apply only when `len(ShowInfo.seasons)` equals the number of Sonarr (or lookup) seasons with number ≥ 1.
- **Without the guard:**
  - `year` comes from the earliest Sonarr `air_date_utc` in that season, when episodes are known, else None;
  - `label` and `next_air_date` are None.

- [ ] **Step 1: Write failing tests**
  - `test_tv_parses_seasons_and_next_episode`. The JoJo-like fixture has seasons 0..6, with season 6 "STEEL BALL RUN" 2026, and `next_episode_to_air` = (6, 10, "2026-11-20"). Assert `seasons[5] == (6, "STEEL BALL RUN", 2026)` and `next_episode == (6, 10, "2026-11-20")`.
  - `test_page_labels_and_next_air_date_when_counts_match`: season 6 has label "STEEL BALL RUN", year 2026 and next_air_date "2026-11-20". The other seasons have next_air_date None.
  - `test_page_generic_season_name_hidden`: TMDB name "Season 2" gives label None, with the year kept.
  - `test_page_labels_hidden_when_season_counts_differ`.
- [ ] **Step 2: Run** `cd app && .venv/bin/python -m pytest -q tests/test_tmdb.py tests/test_shows_service.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the backend. Then in SeasonRow, under the season title, show `label · year` (or just `year`). On the season with `next_air_date`, show "New episode {date}" / "Nouvel épisode {date}", with the date formatted as a localized weekday plus day and month, e.g. "Fri 20 Nov" / "ven. 20 nov.".
- [ ] **Step 4: Run** the backend suite and `cd app/web && npm run check && npm run build`. Expected: PASS, 0 errors.
- [ ] **Step 5: Commit** `feat: name show seasons and show the next air date`

### Task 2: Media probe and codec strings

**Files:**
- Create: `app/cinema/media.py`
- Modify: `app/Dockerfile` (in the Python stage, `apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*` before the pip layer)
- Test: `app/tests/test_media.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) MediaInfo(duration: float, container: str, video_codec: str, codec_string: str, audio_codec: str | None, keyframes: tuple[float, ...])`:
    - `container` is `"mp4"` or `"mkv"`, from the file extension and ffprobe `format_name`;
    - `video_codec` is `"h264"` or `"hevc"` (other codecs are allowed through as their ffprobe name);
    - `keyframes` are sorted video keyframe times in seconds, starting at 0.0.
  - `codec_string(stream: dict) -> str` from an ffprobe video stream. Exact expected values:
    - H.264 High (profile "High") level 40 → `"avc1.640028"`;
    - H.264 Main level 31 → `"avc1.4d401f"`;
    - HEVC Main 10 level 120 → `"hvc1.2.4.L120.90"`;
    - HEVC Main level 123 → `"hvc1.1.6.L123.90"`.
  - `class MediaError(Exception)`.
  - `class Prober(cache_dir: Path, run: Callable[[list[str]], Awaitable[str]] = run_tool)`:
    - `async probe(path: Path) -> MediaInfo`;
    - the result is cached as JSON in `cache_dir`, keyed by sha1 of `f"{path}|{size}|{mtime_ns}"`;
    - a cache hit makes no `run` call;
    - concurrent probes of the same file share one run;
    - a tool failure raises `MediaError`.
  - `async run_tool(args: list[str]) -> str`: an asyncio subprocess that returns stdout and raises `MediaError` on a non-zero exit.
  - The keyframe index comes from `ffprobe -select_streams v:0 -skip_frame nokey -show_entries frame=pts_time -of csv=p=0`.

- [ ] **Step 1: Write failing tests**
  - `test_codec_strings` (the four exact values above).
  - `test_probe_parses_and_caches`: a fake `run` returns canned JSON plus keyframe CSV. A second probe makes no run call, and a changed mtime re-probes.
  - `test_probe_failure_is_media_error`.
  - `test_concurrent_probes_share_one_run`.
  - `test_real_ffprobe_on_generated_sample`: skip without ffmpeg. Generate a 12 s H.264 + AC3 MKV with keyframes every 2 s using lavfi `testsrc`+`sine` in `tmp_path`. Assert duration ≈ 12, the keyframes are `[0,2,4,6,8,10]` ±0.05, `audio_codec == "ac3"`, and the codec string starts with `"avc1."`.
- [ ] **Step 2: Run** `cd app && .venv/bin/python -m pytest -q tests/test_media.py`. Expected: FAIL.
- [ ] **Step 3: Implement** `media.py` and the Dockerfile line.
- [ ] **Step 4: Run** the backend suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat: probe media files and their keyframes`

### Task 3: HLS playlists and capped ffmpeg sessions

**Files:**
- Create: `app/cinema/hls.py`
- Test: `app/tests/test_hls.py`

**Interfaces:**
- Consumes: `MediaInfo` and `MediaError` (Task 2).
- Produces:
  - `plan_segments(keyframes: tuple[float, ...], duration: float, target: float = 6.0) -> list[tuple[float, float]]` as `(start, length)`:
    - walk the keyframes and start a new segment at the first keyframe ≥ `previous start + target`;
    - the last segment runs to `duration`;
    - every start is a keyframe, and the lengths sum to the duration.
  - `render_playlist(segments, *, init_uri: str, seg_uri: Callable[[int], str]) -> str`:
    - `#EXTM3U`, `#EXT-X-VERSION:7`, `#EXT-X-PLAYLIST-TYPE:VOD`, `#EXT-X-INDEPENDENT-SEGMENTS`;
    - `#EXT-X-TARGETDURATION` = ceil(max length);
    - `#EXT-X-MAP:URI="{init_uri}"`;
    - `#EXTINF:{len:.3f},` + URI per segment;
    - `#EXT-X-ENDLIST`.
  - `class BusyError(Exception)`.
  - `class StreamSessions(*, tmp_root: Path, runner: Runner = FfmpegRunner(), max_sessions: int = 2, idle_s: float = 60.0, now: Callable[[], float] = time.monotonic)`:
    - `async init(key: tuple[str, str], path: Path, info: MediaInfo) -> Path` returns the init segment file;
    - `async segment(key, path, info, n: int) -> Path`. `key` is `(device_id, item_id)`.
    - `reap()` kills sessions idle ≥ `idle_s` and removes their dirs.
    - `async close_all()`.
    - `async run_reaper(interval=10.0)` is a loop the app starts.
  - **Runner protocol:**
    - `start(path: Path, info: MediaInfo, segments: list[tuple[float,float]], first: int, out_dir: Path) -> Proc`;
    - `Proc` has `.done() -> bool` and `.kill()`.
    - The default `FfmpegRunner` builds the command below and runs `nice -n 10 ffmpeg ...`.
- **Session rules:**
  - A segment request is served when its file is complete. "Complete" means the next segment file exists, or the process has finished.
  - If segment n is behind the running process's start, or more than 3 segments ahead of the newest written one, restart the process at n.
  - Wait for the file up to 30 s, then raise `MediaError`.
  - A new key with 2 live sessions raises `BusyError`, unless the same `device_id` already has a session, which is replaced.
- **ffmpeg command** (exact flags left to the implementer, these outcomes required):
  - starts at `segments[first][0]` with a pre-input `-ss`;
  - maps the first video and the first audio stream;
  - `-c:v copy`, `-c:a aac -ac 2 -b:a 192k`;
  - HLS output with fMP4 segments named `{n}.m4s` starting at `first`, and init `init.mp4`;
  - cuts at exactly the planned boundaries (e.g. `-hls_time` with `-hls_flags` plus the planned times, or the segment muxer with `-segment_times`);
  - output timestamps continuous with the source timeline (e.g. `-copyts`), so segments from a restart line up.

  The real-ffmpeg test is the arbiter.

- [ ] **Step 1: Write failing tests**
  - `test_plan_segments_on_keyframes`: keyframes `[0,2,4,…,20]`, duration 21, target 6 give `[(0,6),(6,6),(12,6),(18,3)]`.
  - `test_plan_segments_irregular_keyframes`: keyframes `[0,5,7,13,14]`, duration 20 give `[(0,7),(7,6),(13,7)]`.
  - `test_render_playlist_vod`: the exact lines above.
  - `test_third_session_busy`.
  - `test_same_device_replaces_session`.
  - `test_idle_session_killed_and_cleaned`: fake clock and fake runner. After 60 s with no request the proc is killed and the dir removed.
  - `test_seek_restarts_at_segment`: the fake runner records starts. Requesting segment 0 then segment 40 gives two starts, `first=0` then `first=40`. Requesting 41 next does not restart.
  - `test_real_ffmpeg_segment_times_match_playlist`: skip without ffmpeg. Use a 30 s generated H.264+AC3 MKV with keyframes every 2 s.
    - Fetch segment 0, then segment 4 (which forces a restart).
    - With ffprobe, assert each segment's first video pts equals its planned start ±0.1 s.
    - Assert its audio codec is aac with 2 channels.
- [ ] **Step 2: Run** `cd app && .venv/bin/python -m pytest -q tests/test_hls.py`. Expected: FAIL.
- [ ] **Step 3: Implement** `hls.py`.
- [ ] **Step 4: Run** the backend suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat: stream keyframe-aligned HLS with capped ffmpeg sessions`

### Task 4: Play API (info, direct file, HLS routes) and wiring

**Files:**
- Create: `app/cinema/play.py`
- Modify: `app/cinema/main.py` (create the Prober with `cache_dir=/config/media-cache`, create StreamSessions with `tmp_root=/tmp/cinema-hls`, start `run_reaper`, call `close_all` on shutdown, include the router under `/api`), `app/cinema/config.py` (`media_cache_dir`, `hls_tmp_dir`, with those defaults and env `MEDIA_CACHE_DIR`/`HLS_TMP_DIR`), `app/tests/conftest.py`
- Test: `app/tests/test_play.py`

**Interfaces:**
- Consumes: Tasks 2–3, `Store`, `ShowsTracker.entries` / `EpisodeEntry` (state `done`, `path`, `season`, `episode`, `last_episode`, `show_tmdb_id`, `title`).
- Produces `build_play_router(store, tracker, prober, sessions) -> APIRouter`. Item ids are `movie-{tmdb_id}` and `ep-{show}-{season}-{episode}`. The routes:

  | Route | What it does |
  |---|---|
  | `GET /play/movie/{tmdb_id}` and `GET /play/episode/{show_id}/{season}/{episode}` | Returns `{"title","subtitle","duration","container","video_codec","codec_string","audio_codec","direct_url","hls_url"}`. `subtitle` is null for films and `"S01E02"` for episodes. `direct_url` is `.../file` for mp4/m4v, else null. `hls_url` is `.../index.m3u8`. |
  | `GET .../file` | `FileResponse` with Range support, only for mp4/m4v. MKV gives 404. |
  | `GET .../index.m3u8?d={device_id}` | The playlist. Its init and segment URIs carry the same `d`. It counts as a session start, so it is subject to the cap (429). |
  | `GET .../init.mp4?d=` and `GET .../seg/{n}.m4s?d=` | Segment files, served with content types `video/mp4` / `video/iso.segment`. |

  - `device_id` must match `^[A-Za-z0-9-]{8,64}$`, else 400.
  - An episode resolves to a tracker entry whose season matches and whose range `episode..last_episode` contains the requested episode.

- [ ] **Step 1: Write failing tests**
  - `test_play_info_movie_direct_mp4` and `test_play_info_mkv_has_no_direct_url`.
  - `test_play_info_episode_from_tracker`.
  - `test_play_unknown_or_unfinished_is_404`: an unknown id, a `downloading` film, a missing file, a tracker entry not `done`, and an episode outside the range.
  - `test_file_route_serves_ranges_mp4_only`.
  - `test_playlist_and_segments_through_fake_sessions`.
  - `test_busy_is_429`.
  - `test_bad_device_id_is_400`.
  - `test_media_error_is_502_player`.
- [ ] **Step 2: Run** `cd app && .venv/bin/python -m pytest -q tests/test_play.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the router and wiring.
- [ ] **Step 4: Run** the backend suite. Expected: PASS.
- [ ] **Step 5: Commit** `feat: play finished films and episodes over HTTP`

### Task 5: Player page and Play buttons

**Files:**
- Create: `app/web/src/screens/Player.svelte`, `app/web/src/lib/player.ts` (device id, resume storage, playback choice)
- Modify: `app/web/package.json` (`"hls.js": "1.7.4"`), `app/web/src/lib/router.svelte.ts` (`#/play/movie/{id}`, `#/play/episode/{show}/{s}/{e}`, pushed over the current tab, cold-open belongs to Library), `app/web/src/App.svelte`, `app/web/src/screens/Movie.svelte` (Play button for `done`), `app/web/src/components/SeasonRow.svelte` (a play icon on `on_tv` episodes), `app/web/src/lib/api.ts`, `app/web/src/lib/i18n.ts`

**Interfaces:**
- Consumes: the Task 4 endpoints.
- **`player.ts`:**
  - `deviceId()`: a random UUID kept in localStorage `cinema-device`, wrapped in try/catch, with an in-memory fallback.
  - `savedPosition(item)` / `savePosition(item, t)` / `clearPosition(item)` with the key `cinema-resume:{item}`. A position within the last 3 % of the duration counts as finished and is cleared.
  - `chooseSource(info, video) -> "direct" | "hls" | "unsupported"`:
    - "direct" when `direct_url` is set and `video.canPlayType('video/mp4; codecs="{codec_string}, mp4a.40.2"')` is non-empty, or with the file's audio codec mapped (`ac-3`/`ec-3`) for Dolby;
    - "hls" when native HLS is supported, or `MediaSource.isTypeSupported('video/mp4; codecs="{codec_string}"')` is true;
    - "unsupported" otherwise.
- **Player page:**
  - full-screen black, a back button (falls back to the item's page);
  - title and subtitle, and a `<video playsinline controls autoplay>`;
  - "Getting ready…" while the info endpoint loads (the first probe can take tens of seconds);
  - a Resume / Start over choice when a position is saved;
  - the position is saved every 10 s and on pause, `visibilitychange` and leaving;
  - hls.js is imported only when native HLS is missing;
  - the hls.js instance and the video src are destroyed on leave, so the server session goes idle.
  - Error copy for 429 (busy line) and for unsupported (can't-play line).
- **New copy, en / fr:**

  | en | fr |
  |---|---|
  | Play | Lecture |
  | Resume from {t} | Reprendre à {t} |
  | Start over | Reprendre du début |
  | Two people are already watching in a browser — try again in a bit | Deux personnes regardent déjà dans un navigateur — réessayez dans un moment |
  | This one can't play in this browser — try Safari, or watch it on the TV | Impossible de lire ce titre dans ce navigateur — essayez Safari, ou regardez-le sur la TV |

  `{t}` is formatted `h:mm:ss` or `m:ss`.

- [ ] **Step 1:** `player.ts`, with a small pure-function check runnable in the smoke harness (chooseSource cases: mp4+h264 direct; mkv hls; hevc unsupported when both checks are false).
- [ ] **Step 2:** The Player page, routes, Play buttons and i18n. Run `npm install hls.js@1.7.4 --save-exact` in `app/web`.
- [ ] **Step 3: Run** `cd app/web && npm run check && npm run build`. Expected: 0 errors. hls.js is in a separate lazy chunk, not the main bundle (check the build output).
- [ ] **Step 4: Smoke.** Use a fake backend serving a generated sample through the real Task 4 router, or canned responses. Take headless Chromium screenshots at 390×844, en and fr, of:
  - the film page with Play;
  - an episode row with play;
  - the player loading, playing and showing the Resume prompt;
  - the busy and unsupported messages (`canPlayType` stubbed to `""`).

  Check there are zero console errors, and that leaving the player stops segment requests.
- [ ] **Step 5: Commit** `feat: play films and episodes in the browser`

### Task 6: Tailscale in the stack, setup and README

**Files:**
- Modify: `docker-compose.yml`, `setup.py`, `.env.example` (`TS_AUTHKEY=` optional, with a comment), `README.md`
- Test: `tests/test_setup.py`

**Interfaces:**
- **Compose:** a new `tailscale` service:
  - `image: tailscale/tailscale:v1.104.7`, `container_name: tailscale`, `network_mode: host`;
  - env `TS_HOSTNAME=cinema`, `TS_USERSPACE=true`, `TS_STATE_DIR=/var/lib/tailscale`, `TS_AUTHKEY=${TS_AUTHKEY:-}`, `TS_ACCEPT_DNS=false`;
  - volume `${CONFIG_PATH}/tailscale:/var/lib/tailscale`, `restart: unless-stopped`.
  - Funnel/serve are not configured.
- **`setup.py`** (Python 3.8 stdlib):
  - `CONFIG_SERVICES` gains `"tailscale"`.
  - New `tailscale_status() -> dict | None`: `sudo docker exec tailscale tailscale status --json`, parsed; None on failure.
  - New `tailscale_login_url(status) -> str | None`: `AuthURL` when `BackendState` is `"NeedsLogin"`.
  - New `tailscale_address(status) -> str | None`: `Self.DNSName` without the trailing dot, else the first `TailscaleIPs` entry.
  - New `ACCESS_POLICY_SNIPPET` constant: a Tailscale policy `grants` (or `acls`) example that allows the family group and shared users to reach only `tcp:8888` on `tag:cinema` / host `cinema`, with a one-line comment to paste it in the admin console.
  - In `main()` after the stack is up:
    - print the login URL when one is needed (waiting up to 120 s for it to appear);
    - print the Tailscale address for phones away from home;
    - print the access-policy snippet.
  - Never print `TS_AUTHKEY`. A Tailscale problem prints a warning and does not fail setup.
- **README:** a section "Watching away from home" covering:
  - Tailscale on each device;
  - inviting family or sharing the `cinema` machine;
  - the access policy (paste the snippet);
  - `cinema.local` at home and the Tailscale name away, and that resume doesn't carry over between them;
  - that upload speed matters;
  - Funnel off, and never port-forward.

  Also a short "Watching in the browser" section: Play on finished items, HEVC needs Safari or a recent Chrome, 2 browser viewers at once, no subtitles in the browser.

- [ ] **Step 1: Write failing tests**
  - `test_compose_tailscale_pinned_userspace`.
  - `test_tailscale_login_url_and_address`: canned `status --json` for NeedsLogin and for Running.
  - `test_tailscale_failure_warns_not_fails`.
  - `test_access_policy_mentions_only_8888`.
  - `test_authkey_never_printed`.
- [ ] **Step 2: Run** `cd /home/marlon/Projects/Cinema && python3 -m unittest discover -s tests -q`. Expected: FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the setup and backend suites. Expected: PASS.
- [ ] **Step 5: Commit** `feat: reach the cinema app over Tailscale`

---

## After the plan (controller, with the user's permission)
1. Deploy, then run setup. Open the login link and sign in. Paste the access policy.
2. Check that Infuse is unaffected.
3. On an iPhone, play Obsession (direct play) and Magnolia (HEVC, through HLS). On a laptop with Chrome, play 2001 (H.264 MKV, through HLS). Seek far in each, then close the tab and check that ffmpeg exits within about 70 s.
4. Over Tailscale on mobile data, open the Tailscale address and play.
5. Run `docker stats`: memory stays within budget with 2 browser streams.
6. The JoJo page shows "Steel Ball Run · 2026" and "New episode Fri 20 Nov".
