# Browser playback, at home and away (Tailscale)

Full design. Agreed in conversation on 2026-11-15.

## Intent
Today the family watches only through Infuse on the Apple TV. They want to open the Cinema web app on any phone or laptop, go to the Library or a downloaded film or episode, tap **Play** and watch in the browser. They want this at home and also away from home ("from home or outside home").

## Decisions taken by the user
- **Remote access: Tailscale (option A).** Each device installs the Tailscale app once and signs in. The NAS joins the same private network. Nothing is exposed to the public internet, and the app keeps having no login.
- **Playback: original quality, repackaged on the fly (option A).** The video stream is never re-encoded. Only the audio is converted to a browser-friendly format, and the container is changed. There is no "Data saver" or reduced-quality mode.

## Goals
- A **Play** button wherever a finished film or episode is shown:
  - the film page when the film is on the TV (state `done`);
  - each episode in a show page's episode list whose state is `on_tv`;
  - Library cards stay as they are (they open the film or show page, which has Play).
- A full-screen player page (`#/play/movie/{tmdb_id}` and `#/play/episode/{show_tmdb_id}/{season}/{episode}`). It has:
  - a back button, the title (and SxxEyy for episodes), and the browser's native controls;
  - working seeking anywhere in the file, which is required;
  - start within a few seconds.
- **Resume:** each device remembers where each item was left (browser storage, per device) and offers "Resume from 42:10" / "Start over". This is my proposal; it is cheap.
- **Same app at home and away.** Over Tailscale the phone opens the NAS by its Tailscale name. Every feature works there, not only playback.
- **Formats:**
  - H.264 files must play in Safari (iPhone, iPad, Mac), Chrome (Android, desktop), Firefox and Edge.
  - HEVC files (including 10-bit, e.g. Magnolia, The Agency) must play where the device supports HEVC: Safari on Apple devices, and Chrome/Edge with hardware support.
  - Where the browser cannot play the video, show a friendly line instead of a black screen: "This one can't play in this browser — try Safari, or watch it on the TV" (fr: "Impossible de lire ce titre dans ce navigateur — essayez Safari, ou regardez-le sur la TV").
- **Audio** is converted to AAC stereo (downmixed from 5.1/7.1) for every file, because browsers don't reliably play Dolby Digital, DD+, DTS or TrueHD.
- **Admin setup for Tailscale is scripted** like the rest of the stack, and the README explains how family members get access.

## Non-goals
- Re-encoding video (a lower-quality or "data saver" mode). It was explicitly not chosen.
- Playing an item that is still downloading. Infuse remains the way to watch while downloading; the browser plays finished files only.
- Subtitles in the browser player in v1. Infuse handles subtitles on the TV.
- Picking audio tracks or languages. Use the first/default audio track.
- Casting to a TV from the browser, and offline downloads.
- A login or accounts in the app.

## Anti-goals (a failure even if it works)
- The Cinema app or the NAS becomes reachable from the public internet. Port forwarding, DSM QuickConnect, Funnel or a public reverse proxy must never be set up for this.
- Playback in the browser interrupts or slows a film playing in Infuse on the TV.
- The NAS runs out of memory. Today about 460 MB is available.
- A stream keeps converting after the viewer closed the page.
- Converted copies pile up on disk.

## Constraints
- **NAS:** DS720+ with a 4-core Celeron J4125, 1.75 GB RAM, about 460 MB available after cinema, rqbit, Sonarr and Prowlarr.
  - **Cap: at most 2 browser streams at once.** A third viewer sees "Two people are already watching in a browser — try again in a bit" (fr equivalent).
  - Each stream may use about 100 MB at most.
- **Tools:**
  - The conversion uses `ffmpeg`/`ffprobe`, installed in the cinema image (the Dockerfile's Python stage). No other new server dependency is allowed.
  - The phone app currently has no runtime dependency beyond svelte. The **one** allowed exception is `hls.js`, loaded lazily only on the player page and only when the browser lacks native HLS playback (Safari has it; Chrome/Firefox desktop may not). This is my call: without it, seeking in a repackaged stream does not work in those browsers.
- **Inputs:**
  - Files are MKV or MP4. Video is H.264 or HEVC (8/10-bit). Audio is AC3, E-AC3/DD+, DTS, FLAC, AAC, or TrueHD.
  - Film files come from `Store` (`file_path`) or the legacy folder. Episode files come from the tracker's on-disk entries (`EpisodeEntry.path`, state `done`).
  - The app must refuse any path outside those known files.
- **Existing behaviour stays:**
  - films/shows flows, WebDAV for Infuse, `/api` shapes (only additions), en/fr copy;
  - "ask before deploying" (a cinema restart interrupts Infuse).
- **Tailscale:**
  - Runs as a container in the compose stack: `tailscale/tailscale`, host network, state in `${CONFIG_PATH}/tailscale`, pinned version.
  - Use kernel or userspace networking, whichever works on DSM 7.2 without manual kernel module steps. The builder verifies.
  - The NAS's tailnet hostname is `cinema`.
  - Setup prints the one-time login link (or uses an optional `TS_AUTHKEY` from `.env`) and never prints keys.
  - Tailscale Funnel stays off.
  - About 30–50 MB of memory.
- **Family access:** the README explains the two ways people reach the NAS on Tailscale:
  - sign into the admin's tailnet (the free plan allows a few users);
  - or **share the `cinema` machine** with each person's own Tailscale account (node sharing).

  Either way the address to open is the Tailscale name, e.g. `http://cinema:8888` with MagicDNS.

## Approaches
**Remote access:**
- **A) Tailscale (chosen).** Private, nothing public, no login to build. The cost is one app install per device.
- **B) A public HTTPS address with a new login in the app.** It works from any browser with no install, but it exposes an app that can start downloads and delete files to the internet, and it needs a solid login. Rejected.
- **Cloudflare Tunnel.** Dropped: its terms forbid streaming video over the free tunnel.

**Playback:**
- **A) Original quality: video copied, audio converted, served as HLS (chosen).** It is light, about one small ffmpeg process per viewer. The cost: HEVC plays only where the device supports it, and away from home it needs upload bandwidth equal to the file's bitrate (about 5–15 Mbps for 1080p).
- **B) Add hardware video conversion with Intel Quick Sync for a "Data saver" mode.** It covers slow connections and browsers without HEVC, but costs 150–250 MB per stream. Not chosen.
- **Within A, how to serve it:**
  - (1) Convert the whole file to a cached MP4 before playing. Perfect seeking with plain `<video>`, but a wait of a minute or more and a second copy on disk. Rejected.
  - (2) On-the-fly HLS. It starts in seconds and uses no extra disk. **Chosen** (my call).

## Design
**Server (cinema app):**
- **Probe:**
  - On first Play of a file, `ffprobe` reads duration, codecs and the **keyframe timestamps** of the video stream.
  - The result is cached on disk in `/config` (small JSON per file, keyed by path, size and mtime) so later plays start instantly.
  - The probe may take tens of seconds for a long film the first time. The player shows "Getting ready…" meanwhile.
- **Playlist:**
  - `GET /api/play/{kind}/{id...}/index.m3u8` returns a **VOD** HLS playlist (fMP4 segments, `#EXT-X-PLAYLIST-TYPE:VOD`, `#EXT-X-ENDLIST`).
  - Segment boundaries sit on **real keyframes**, about 6 s apart. They must be keyframes because the video is copied, not re-encoded.
  - fMP4 (not MPEG-TS) is used so HEVC works in Safari (`hvc1` tag).
- **Segments:**
  - `GET .../seg/{n}.m4s` (plus the init segment) is produced by **one ffmpeg process per viewer session**:
    - it starts at segment n's keyframe time;
    - the video is copied, the audio is converted to AAC stereo 192 kb/s;
    - it writes segments into a per-session temp dir under `/tmp` (tmpfs is not required).
  - A request for a segment far from what the running process is producing (a seek) restarts ffmpeg at that segment.
  - A request for an already-written segment is served from the temp dir.
- **Sessions:**
  - A session is identified by a random id in the URL.
  - The ffmpeg process is killed and its temp dir deleted when no segment has been requested for 60 s, when the session is replaced, or on app shutdown.
  - At most 2 sessions run at once (the cap above). A new session for the same device and item replaces the old one rather than counting twice.
- **Info endpoint:** `GET /api/play/{kind}/{id...}` returns:
  - title, SxxEyy, duration;
  - `video_codec` (`h264`/`hevc`) plus the codec string the browser needs for `MediaSource.isTypeSupported` / `canPlayType` (e.g. `hvc1.2.4.L120.90`, `avc1.640028`);
  - the playlist URL.
- **Errors:**
  - an unknown item or a file not on disk gives 404;
  - the stream cap gives 429 `{"error":"busy"}`;
  - an ffmpeg failure gives 502 `{"error":"upstream","service":"player"}`.
- **Low priority:** ffmpeg runs at low CPU priority (`nice`) so rqbit and WebDAV serving to Infuse are never starved.

**Phone app (Svelte):**
- A **Play** button (primary, large) on the film page for `done` films. On a show page, each `on_tv` episode in the expanded list gets a play icon.
- The **player page**:
  - a full-screen black page with a `<video playsinline controls>`;
  - native HLS when `video.canPlayType('application/vnd.apple.mpegurl')` is truthy, otherwise hls.js loaded with a dynamic `import()`;
  - before loading, it checks codec support with the info endpoint's codec string and shows the "can't play in this browser" line if unsupported;
  - a Resume / Start over choice when a saved position exists;
  - the position is saved every 10 s and on pause or exit;
  - a saved position within the last 3 % counts as finished and is cleared.
- **New en/fr copy:**
  - Play / Lecture
  - Resume from {t} / Reprendre à {t}
  - Start over / Reprendre du début
  - Getting ready… (existing key)
  - the can't-play line and the busy line (above).

**Tailscale (stack, setup, README):**
- A new `tailscale` service in compose, pinned, with host network and state volume. Its hostname is `cinema`.
- `setup.py`:
  - creates the state dir;
  - after the stack is up, checks `tailscale status`;
  - if not logged in, prints the login URL and waits up to a few minutes, or tells the admin to re-run setup after signing in;
  - prints the Tailscale address for phones.
- README section "Watching away from home":
  - install Tailscale and sign in;
  - how to add family members (invite, or share the `cinema` machine);
  - open `http://cinema:8888`, and add it to the home screen too;
  - a note that playing away from home needs good upload speed at home;
  - Funnel must stay off.

## Left to the builder
- The exact ffmpeg/ffprobe command lines, segment length (about 6 s), and how a seek decides to restart ffmpeg versus wait for it.
- How Tailscale runs on DSM 7.2 (kernel tun versus userspace networking), and whether `tailscale serve` (HTTPS on the ts.net name) is worth adding.
- The probe cache format and its cleanup.
- The visual details of the player page and the Play buttons, following the existing look.
- The test strategy:
  - unit tests for playlist building (keyframe segmentation), session lifecycle and the cap, and path safety, using a fake ffmpeg runner;
  - one real ffmpeg integration test on a tiny generated sample, skipped if ffmpeg is absent locally;
  - smoke screenshots for the player.
