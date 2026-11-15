<script lang="ts">
  import { fade } from "svelte/transition";
  import ErrorBlock from "../components/ErrorBlock.svelte";
  import Icon from "../components/Icon.svelte";
  import SeasonRow from "../components/SeasonRow.svelte";
  import Spinner from "../components/Spinner.svelte";
  import { api, backdropUrl, posterUrl, type ShowInfo, type ShowPage } from "../lib/api";
  import { catalog } from "../lib/catalog.svelte";
  import { t, yearsText } from "../lib/i18n";
  import { ms } from "../lib/motion";
  import { goBack, tabHref } from "../lib/router.svelte";

  // The show page, pushed over the current tab. Every action answers with the whole
  // page, which replaces what is shown; a failed one leaves it as it was.
  let { id }: { id: number } = $props();

  const POLL_MS = 5000; // while a season is being looked for or downloaded
  const ACTION_TIMEOUT_MS = 60000; // a first Follow waits for Sonarr's refresh

  let page = $state<ShowPage | null>(null);
  let loadFailed = $state(false);
  let artLoaded = $state(false);
  /** The request in flight: "follow", "unfollow", or "get-3" / "remove-3" for a season. */
  let pending = $state<string | null>(null);
  /** Where the last request failed: "follow" or a season number. */
  let failed = $state<"follow" | number | null>(null);
  let unfollowing = $state(false);

  const show = $derived(page?.show ?? null);
  const years = $derived(show ? yearsText(show.year, show.end_year, show.airing) : null);
  const busy = (data: ShowPage | null) =>
    !!data?.seasons.some((s) => s.state === "looking" || s.state === "downloading");

  // Only the newest request's answer is shown: an action outranks a poll that was
  // already on its way.
  let latest = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let gone = false;

  function display(data: ShowPage) {
    if (gone) return; // an answer that lands after the page was closed
    page = data;
    document.title = `${data.show.title} · Cinema`;
    // Downloads (and its badge) learn about new episodes from /api/home, whose 3 s
    // poll only runs once it knows something is getting ready.
    if (data.seasons.some((s) => s.state === "downloading") && !catalog.episodes.length) catalog.refresh();
  }

  async function load() {
    loadFailed = false;
    const mine = ++latest;
    try {
      const data = await api<ShowPage>(`/shows/${id}`);
      if (mine === latest) display(data);
    } catch {
      if (mine === latest && !page) loadFailed = true;
    }
    schedule();
  }

  /** At most one pending re-fetch; none while hidden, idle or acting. */
  function schedule() {
    if (timer || gone || document.hidden || pending || !busy(page)) return;
    timer = setTimeout(async () => {
      timer = null;
      if (!pending) await load();
    }, POLL_MS);
  }

  function stop() {
    if (timer) clearTimeout(timer);
    timer = null;
  }

  $effect(() => {
    const visibility = () => {
      if (document.hidden) stop();
      else if (busy(page) && !pending) load();
    };
    document.addEventListener("visibilitychange", visibility);
    return () => {
      gone = true;
      stop();
      document.removeEventListener("visibilitychange", visibility);
      document.title = "Cinema";
    };
  });

  load();

  async function act(key: string, where: "follow" | number, method: "POST" | "DELETE", path: string): Promise<boolean> {
    if (pending) return false;
    pending = key;
    failed = null;
    stop();
    const mine = ++latest;
    let ok = false;
    try {
      const data = await api<ShowPage>(`/shows/${id}${path}`, { method, timeout: ACTION_TIMEOUT_MS });
      if (mine === latest) display(data);
      ok = true;
      catalog.refresh();
    } catch {
      failed = where;
    }
    pending = null;
    schedule();
    return ok;
  }

  const getSeason = (n: number) => act(`get-${n}`, n, "POST", `/seasons/${n}`);
  const removeSeason = (n: number) => act(`remove-${n}`, n, "DELETE", `/seasons/${n}`);
  const follow = () => act("follow", "follow", "POST", "/follow");
  async function unfollow() {
    if (await act("unfollow", "follow", "DELETE", "/follow")) unfollowing = false;
  }

  const art = (s: ShowInfo) => (s.backdrop_path ? backdropUrl(s.backdrop_path) : null);
  const seasonPending = (n: number) =>
    pending === `get-${n}` ? "get" : pending === `remove-${n}` ? "remove" : null;
</script>

<article class="show" in:fade={{ duration: ms(200) }}>
  <!-- Opened from a link, back lands on the Shows tab. -->
  <button class="back" type="button" aria-label={t("back")} onclick={() => goBack(tabHref("shows"))}>
    <Icon name="back" size={22} stroke={2.4} />
  </button>

  {#if loadFailed}
    <div class="failed-load"><ErrorBlock retry={load} /></div>
  {:else if !page || !show}
    <div class="backdrop" aria-hidden="true"></div>
    <div class="body" aria-hidden="true">
      <div class="bone bone--title"></div>
      <div class="bone bone--meta"></div>
      <div class="bone bone--season"></div>
      <div class="bone bone--season"></div>
      <div class="bone bone--season"></div>
    </div>
  {:else}
    <div class="backdrop" class:bare={!show.backdrop_path && !show.poster_path}>
      {#if art(show)}
        <img src={art(show)} alt="" class:loaded={artLoaded} onload={() => (artLoaded = true)} decoding="async" />
      {:else if show.poster_path}
        <img class="blur loaded" src={posterUrl(show.poster_path)} alt="" />
        <img class="poster" src={posterUrl(show.poster_path)} alt="" />
      {/if}
    </div>

    <div class="body">
      <h1 class="title">{show.title}</h1>
      {#if years}<p class="meta">{years}</p>{/if}

      {#if !page.available}
        <p class="unavailable"><Icon name="info" size={18} /><span>{t("not_available")}</span></p>
      {:else if show.airing}
        <div class="follow-area" aria-live="polite">
          {#if unfollowing && page.following}
            <div class="unfollow-confirm">
              <button class="btn btn--danger" type="button" disabled={pending != null} onclick={unfollow}>
                {#if pending === "unfollow"}<Spinner size={16} />{/if}{t("unfollow")}
              </button>
              <button class="btn btn--secondary" type="button" disabled={pending != null} onclick={() => (unfollowing = false)}>{t("cancel")}</button>
            </div>
          {:else}
            <button class="btn btn--secondary follow" class:on={page.following} type="button"
                    aria-pressed={page.following} disabled={pending != null}
                    onclick={() => (page?.following ? (unfollowing = true) : follow())}>
              {#if pending === "follow"}<Spinner size={16} />
              {:else}<Icon name={page.following ? "check" : "plus"} size={18} stroke={2.4} />{/if}
              {t(page.following ? "following" : "follow")}
            </button>
          {/if}
          {#if failed === "follow"}<p class="message" role="alert">{t("error")}</p>{/if}
        </div>
      {/if}

      {#if show.overview}
        <p class="overview">{show.overview}</p>
      {/if}

      {#if page.available && page.seasons.length}
        <ol class="seasons" aria-live="polite">
          {#each page.seasons as season (season.number)}
            <SeasonRow {season} show={id} locked={pending != null} pending={seasonPending(season.number)}
                       failed={failed === season.number}
                       onget={() => getSeason(season.number)} onremove={() => removeSeason(season.number)} />
          {/each}
        </ol>
      {/if}
    </div>
  {/if}
</article>

<style>
  .show { position: relative; min-height: 100dvh; }

  .back {
    position: fixed;
    z-index: 20;
    top: calc(env(safe-area-inset-top) + 10px);
    left: 12px;
    width: 44px;
    height: 44px;
    display: grid;
    place-items: center;
    border: 0;
    border-radius: 50%;
    background: rgb(21 17 27 / 0.55);
    -webkit-backdrop-filter: blur(14px);
    backdrop-filter: blur(14px);
    box-shadow: inset 0 0 0 1px rgb(255 255 255 / 0.1);
    color: var(--screen);
    cursor: pointer;
  }
  .back:active { background: rgb(21 17 27 / 0.8); }

  .backdrop {
    position: relative;
    height: min(56vh, 480px);
    overflow: hidden;
    background: linear-gradient(180deg, #2a2133, var(--curtain));
  }
  .backdrop.bare {
    height: calc(env(safe-area-inset-top) + 230px);
    background: radial-gradient(90% 70% at 50% 0%, rgb(243 194 107 / 0.1), transparent 70%), linear-gradient(180deg, #2a2133, var(--curtain));
  }
  .backdrop img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: 50% 25%;
    opacity: 0;
    transition: opacity 0.6s ease;
  }
  .backdrop img.loaded { opacity: 1; }
  .backdrop .blur { position: absolute; inset: -30px; width: calc(100% + 60px); height: calc(100% + 60px); filter: blur(28px) saturate(1.2); opacity: 0.55; }
  .backdrop .poster {
    position: absolute;
    left: 50%;
    top: calc(env(safe-area-inset-top) + 64px);
    width: 132px;
    height: auto;
    transform: translateX(-50%);
    border-radius: var(--r-poster);
    opacity: 1;
    box-shadow: 0 16px 40px rgb(0 0 0 / 0.55);
  }
  .backdrop::after {
    content: "";
    position: absolute;
    inset: 0;
    background:
      linear-gradient(180deg, rgb(21 17 27 / 0.5) 0%, transparent 20%),
      linear-gradient(0deg, var(--curtain) 0%, rgb(21 17 27 / 0.7) 22%, transparent 60%);
  }

  .body {
    position: relative;
    margin-top: -112px;
    padding: 0 var(--gutter) 24px;
    display: flex;
    flex-direction: column;
  }
  .title {
    margin: 0;
    font: 650 clamp(30px, 8.6vw, 42px)/1.06 var(--serif);
    letter-spacing: -0.01em;
    text-wrap: balance;
    text-shadow: 0 2px 24px rgb(0 0 0 / 0.55);
  }
  .meta { margin: 8px 0 0; color: var(--usher); font-size: 15px; font-variant-numeric: tabular-nums; }

  .follow-area { display: flex; flex-direction: column; gap: 8px; margin-top: 18px; }
  .follow { align-self: flex-start; min-height: 44px; padding: 0 18px 0 14px; gap: 8px; font-size: 16px; }
  .follow.on { background: rgb(243 194 107 / 0.12); color: var(--tungsten); }
  .follow:disabled { opacity: 0.6; }
  .unfollow-confirm { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .unfollow-confirm .btn { min-height: 48px; font-size: 16px; }
  .unavailable {
    display: flex;
    align-items: center;
    gap: 10px;
    margin: 18px 0 0;
    padding: 14px 16px;
    border-radius: var(--r-card);
    background: var(--velvet);
    box-shadow: inset 0 0 0 1px var(--line);
    color: var(--usher);
    font-weight: 550;
  }
  .message { margin: 0; font-size: 14px; color: var(--failed); }
  .overview { margin: 22px 0 0; color: #d6cddd; font-size: 16px; line-height: 1.55; max-width: 62ch; }

  .seasons { list-style: none; margin: 28px 0 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }

  .failed-load { padding-top: calc(env(safe-area-inset-top) + 72px); }
  .bone { background: var(--skeleton); border-radius: 8px; }
  .bone--title { width: 70%; height: 36px; }
  .bone--meta { width: 28%; height: 16px; margin-top: 12px; }
  .bone--season { height: 112px; margin-top: 10px; border-radius: var(--r-card); }
  .bone--meta + .bone--season { margin-top: 28px; }
  @media (prefers-reduced-motion: reduce) { .backdrop img { transition: none; } }
</style>
