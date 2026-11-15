<script lang="ts">
  import type Hls from "hls.js";
  import { untrack } from "svelte";
  import { fade } from "svelte/transition";
  import Icon from "../components/Icon.svelte";
  import Spinner from "../components/Spinner.svelte";
  import { api, ApiError } from "../lib/api";
  import { t } from "../lib/i18n";
  import { ms } from "../lib/motion";
  import {
    chooseSource, clearPosition, clock, deviceId, hlsSupported, mediaFailure, nativeHls, playlistOutcome,
    retryAfterMs, savedPosition, savePosition, withTimeout, type PlayInfo, type Preflight, type Source,
  } from "../lib/player";
  import { goBack, type PlayTarget } from "../lib/router.svelte";

  // The full-screen player, over the film or show page it plays. The file itself when
  // the browser plays its codecs, else the server's HLS rendition (native, or hls.js
  // where the browser has no HLS of its own). Leaving stops every request, so the
  // server's stream session goes idle and is closed.
  let { target }: { target: PlayTarget } = $props();

  type Phase = "loading" | "choose" | "playing" | "busy" | "unsupported" | "error";

  // The server answers every request within about 50 s; a long file's first playlist
  // says "preparing" (and when to ask again) until its keyframe index is built.
  const INFO_TIMEOUT_MS = 60000;
  const PLAYLIST_TIMEOUT_MS = 60000;
  const PREPARING_LIMIT_MS = 30 * 60000;
  const SAVE_EVERY_MS = 10000;

  // A player is keyed by its target, which never changes for one instance.
  const { item, infoPath, backHref } = (() => {
    const p = target;
    if (p.kind === "movie") {
      return { item: `movie-${p.id}`, infoPath: `/play/movie/${p.id}`, backHref: `#/movie/${p.id}` };
    }
    return {
      item: `ep-${p.show}-${p.season}-${p.episode}`,
      infoPath: `/play/episode/${p.show}/${p.season}/${p.episode}`,
      backHref: `#/show/${p.show}`,
    };
  })();

  let video: HTMLVideoElement;
  let backButton: HTMLButtonElement;
  let info = $state<PlayInfo | null>(null);
  let phase = $state<Phase>("loading");
  let resumeAt = $state<number | null>(null);

  let source: Source = "unsupported";
  let HlsClass: typeof Hls | null = null;
  let hls: Hls | null = null;
  /** Where to seek once the file's metadata is in (direct and native HLS). */
  let seekTo = 0;
  /** Playback has really started: positions before that are not worth saving. */
  let started = false;
  /** Where the last start() began, for the fallback from direct play to HLS. */
  let startedAt = 0;
  /** Direct play failed before playing and HLS took over (once per load). */
  let fellBack = false;
  /** A native HLS error re-asked for the playlist (once per load). */
  let rechecked = false;
  let gone = false;
  let abort = new AbortController();

  const hlsUrl = (data: PlayInfo) => `${data.hls_url}?d=${encodeURIComponent(deviceId())}`;

  function wait(ms: number, signal: AbortSignal) {
    return new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, ms);
      signal.addEventListener("abort", () => (clearTimeout(timer), resolve()), { once: true });
    });
  }

  /** One playlist request: what it answered, and when to ask again. */
  async function askPlaylist(data: PlayInfo, signal: AbortSignal): Promise<{ outcome: Preflight; retryMs: number }> {
    const timed = withTimeout(signal, PLAYLIST_TIMEOUT_MS);
    try {
      const resp = await fetch(hlsUrl(data), { signal: timed.signal });
      const body = await resp.text();
      return { outcome: playlistOutcome(resp.status, body), retryMs: retryAfterMs(resp.headers.get("Retry-After")) };
    } catch {
      return { outcome: "error", retryMs: 0 };
    } finally {
      timed.clear();
    }
  }

  /**
   * Ask for the playlist until the server has it ready. That starts the stream session,
   * so the cap answers here, and it waits out a long file's keyframe index ("Getting
   * ready…") so hls.js and native players are only handed a URL that answers. Null when
   * the page was left meanwhile.
   */
  async function preflight(data: PlayInfo, signal: AbortSignal): Promise<Exclude<Preflight, "preparing"> | null> {
    const until = Date.now() + PREPARING_LIMIT_MS;
    for (;;) {
      const { outcome, retryMs } = await askPlaylist(data, signal);
      if (gone || signal.aborted) return null;
      if (outcome !== "preparing") return outcome;
      if (Date.now() + retryMs > until) return "error";
      await wait(retryMs, signal);
      if (gone || signal.aborted) return null;
    }
  }

  /** Get the HLS rendition ready to play; false (with the phase set) when it can't be. */
  async function prepareHls(data: PlayInfo, signal: AbortSignal): Promise<boolean> {
    const outcome = await preflight(data, signal);
    if (outcome == null) return false;
    if (outcome !== "ready") {
      phase = outcome;
      return false;
    }
    if (!nativeHls(video)) {
      HlsClass ??= (await import("hls.js/light")).default;
      if (gone) return false;
      if (!HlsClass.isSupported()) {
        phase = "unsupported";
        return false;
      }
    }
    return true;
  }

  async function load() {
    phase = "loading";
    abort.abort(); // an earlier load still polling stops here
    abort = new AbortController();
    fellBack = false;
    rechecked = false;
    const { signal } = abort;
    try {
      const data = await api<PlayInfo>(infoPath, { timeout: INFO_TIMEOUT_MS, signal });
      if (gone) return;
      info = data;
      document.title = `${data.title} · Cinema`;
      source = chooseSource(data, video);
      if (source === "unsupported") return void (phase = "unsupported");
      if (source === "hls" && !(await prepareHls(data, signal))) return;
      resumeAt = savedPosition(item, data.duration);
      if (resumeAt == null) start(0);
      else phase = "choose";
    } catch (err) {
      if (gone || (err instanceof ApiError && err.code === "aborted")) return;
      phase = err instanceof ApiError && err.status === 429 ? "busy" : "error";
    }
  }

  /** Called from a tap where possible, so play() keeps the user gesture. */
  function start(at: number) {
    if (!info || gone) return;
    phase = "playing";
    started = false;
    startedAt = at;
    seekTo = 0;
    if (HlsClass && source === "hls") {
      const Ctor = HlsClass;
      const player = new Ctor({ startPosition: at });
      let recovered = false;
      player.on(Ctor.Events.ERROR, (_event, data) => {
        if (!data.fatal || gone) return;
        if (data.response?.code === 429) return fail("busy");
        if (data.type === Ctor.ErrorTypes.MEDIA_ERROR && !recovered) {
          recovered = true;
          player.recoverMediaError();
          return;
        }
        fail("error");
      });
      player.loadSource(hlsUrl(info));
      player.attachMedia(video);
      hls = player;
    } else {
      seekTo = at;
      video.src = source === "direct" ? info.direct_url! : hlsUrl(info);
    }
    video.play().catch(() => {
      // Autoplay refused: the native controls show a play button.
    });
  }

  function fail(why: "busy" | "error" | "unsupported") {
    save();
    stop();
    phase = why;
  }

  /** Stop the stream: no request leaves this page after this. */
  function stop() {
    hls?.destroy();
    hls = null;
    started = false;
    if (video) {
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
  }

  function save() {
    if (info && started && phase === "playing") savePosition(item, video.currentTime, info.duration);
  }

  function retry() {
    stop();
    load();
  }

  function metadata() {
    if (seekTo > 0) video.currentTime = seekTo;
    seekTo = 0;
  }

  async function mediaError() {
    // Fired for direct play and native HLS; hls.js reports its own errors. A src
    // removed on purpose leaves no error behind.
    if (gone || phase !== "playing" || hls || !video.error || !info) return;
    const code = video.error.code;
    const played = started;
    const data = info;
    const { signal } = abort;
    if (source === "direct" && !started && !fellBack && hlsSupported(data, video)) {
      // The browser said yes to the file but won't play it: try the HLS rendition once.
      fellBack = true;
      const at = startedAt;
      stop();
      source = "hls";
      phase = "loading";
      if (await prepareHls(data, signal)) start(at);
      return;
    }
    if (source === "hls" && !rechecked) {
      // Native HLS hides the server's answers: ask once whether another viewer took the slot.
      rechecked = true;
      const { outcome } = await askPlaylist(data, signal);
      if (gone || signal.aborted || phase !== "playing") return;
      if (outcome === "busy") return fail("busy");
    }
    fail(mediaFailure(code, played));
  }

  $effect(() => {
    const el = video;
    const title = document.title;
    document.body.classList.add("locked");
    const timer = setInterval(() => {
      if (!el.paused) save();
    }, SAVE_EVERY_MS);
    const hidden = () => {
      if (document.hidden) save();
    };
    document.addEventListener("visibilitychange", hidden);
    backButton.focus({ preventScroll: true });
    untrack(load);
    return () => {
      save();
      gone = true;
      abort.abort();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", hidden);
      hls?.destroy();
      hls = null;
      el.pause();
      el.removeAttribute("src");
      el.load();
      document.body.classList.remove("locked");
      document.title = title;
    };
  });
</script>

<div class="player" role="dialog" aria-modal="true" aria-label={info?.title ?? t("play")} in:fade={{ duration: ms(180) }}>
  <header class="top">
    <button bind:this={backButton} class="back" type="button" aria-label={t("back")} onclick={() => goBack(backHref)}>
      <Icon name="back" size={22} stroke={2.4} />
    </button>
    {#if info}
      <div class="heading">
        <h1 class="title">{info.title}</h1>
        {#if info.subtitle}<p class="subtitle">{info.subtitle}</p>{/if}
      </div>
    {/if}
  </header>

  <div class="stage">
    <!-- Subtitles stay with Infuse in v1. -->
    <!-- svelte-ignore a11y_media_has_caption -->
    <video bind:this={video} class:shown={phase === "playing"} playsinline controls autoplay preload="metadata"
           onloadedmetadata={metadata} onplaying={() => (started = true)} onpause={save}
           onended={() => clearPosition(item)} onerror={mediaError}></video>

    {#if phase === "loading"}
      <div class="panel" aria-live="polite">
        <Spinner size={28} />
        <p class="line">{t("preparing")}</p>
      </div>
    {:else if phase === "choose" && resumeAt != null}
      <div class="panel choose">
        <button class="btn btn--primary btn--block btn--big" type="button" onclick={() => start(resumeAt ?? 0)}>
          <span class="glyph"><Icon name="play" size={20} stroke={2} /></span>{t("resume_from", { t: clock(resumeAt) })}
        </button>
        <button class="btn btn--secondary btn--block" type="button" onclick={() => start(0)}>
          <Icon name="restart" size={20} stroke={2.2} />{t("start_over")}
        </button>
      </div>
    {:else if phase === "busy" || phase === "unsupported" || phase === "error"}
      <div class="panel" role="alert">
        <span class="mark"><Icon name={phase === "unsupported" ? "info" : "alert"} size={26} /></span>
        <p class="line">{t(phase === "busy" ? "play_busy" : phase === "unsupported" ? "play_unsupported" : "error")}</p>
        {#if phase !== "unsupported"}
          <button class="btn btn--secondary again" type="button" onclick={retry}>
            <Icon name="retry" size={18} stroke={2.2} />{t("try_again")}
          </button>
        {/if}
      </div>
    {/if}
  </div>
</div>

<style>
  .player {
    position: fixed;
    z-index: 40;
    inset: 0;
    display: flex;
    flex-direction: column;
    background: #000;
    color: var(--screen);
  }

  .top {
    flex: none;
    display: flex;
    align-items: center;
    gap: 12px;
    min-height: 64px;
    padding: calc(env(safe-area-inset-top) + 10px) calc(env(safe-area-inset-right) + var(--gutter)) 10px
      calc(env(safe-area-inset-left) + 12px);
  }
  /* Upright, the picture and the prompts sit in the middle of the screen with the
     header over the black above them; sideways, the header keeps its own row so it
     never covers the picture (or Safari's inline controls). */
  @media (orientation: portrait) {
    .top { position: absolute; z-index: 2; inset: 0 0 auto; }
  }
  .back {
    flex: none;
    width: 44px;
    height: 44px;
    display: grid;
    place-items: center;
    border: 0;
    border-radius: 50%;
    background: rgb(255 255 255 / 0.1);
    color: var(--screen);
    cursor: pointer;
  }
  .back:active { background: rgb(255 255 255 / 0.18); }
  .heading { min-width: 0; }
  .title {
    margin: 0;
    font: 600 18px/1.2 var(--serif);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .subtitle {
    margin: 2px 0 0;
    color: var(--usher);
    font-size: 13px;
    font-weight: 600;
    letter-spacing: 0.04em;
    font-variant-numeric: tabular-nums;
  }

  .stage {
    position: relative;
    flex: 1;
    min-height: 0;
    display: grid;
    place-items: center;
    padding: 0 env(safe-area-inset-right) env(safe-area-inset-bottom) env(safe-area-inset-left);
  }
  video {
    grid-area: 1 / 1;
    width: 100%;
    height: 100%;
    max-height: 100%;
    object-fit: contain;
    background: #000;
    visibility: hidden;
  }
  video.shown { visibility: visible; }

  .panel {
    grid-area: 1 / 1;
    z-index: 1;
    width: min(100% - 2 * var(--gutter), 340px);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 14px;
    text-align: center;
    color: var(--usher);
  }
  .line { margin: 0; font-size: 16px; line-height: 1.45; font-weight: 550; color: var(--screen); text-wrap: balance; }
  .mark { color: var(--tungsten); }
  .choose { gap: 10px; }
  .btn--big { min-height: 56px; font-size: 18px; }
  .glyph { display: contents; }
  .glyph :global(path) { fill: currentColor; }
  .again { margin-top: 4px; min-height: 48px; font-size: 16px; gap: 8px; }
</style>
