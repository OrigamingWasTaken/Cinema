<script lang="ts">
  import { untrack } from "svelte";
  import { fade } from "svelte/transition";
  import ErrorBlock from "../components/ErrorBlock.svelte";
  import Icon from "../components/Icon.svelte";
  import Spinner from "../components/Spinner.svelte";
  import Status from "../components/Status.svelte";
  import { api, backdropUrl, posterUrl, type MovieData, type MovieInfo } from "../lib/api";
  import { catalog } from "../lib/catalog.svelte";
  import { clearMessage, flow, forgetReleases, getRecommended } from "../lib/flows.svelte";
  import { runtimeText, t } from "../lib/i18n";
  import { ms } from "../lib/motion";
  import { goBack, navigate, router } from "../lib/router.svelte";
  import MoreSheet from "./MoreSheet.svelte";

  // The film page, pushed over the current tab. What is on the server comes from the
  // shared catalog, so the Downloads tab and this page always agree.
  let { id, sheet }: { id: number; sheet: boolean } = $props();

  let movie = $state<MovieInfo | null>(null);
  let loadFailed = $state(false);
  let artLoaded = $state(false);
  let confirming = $state(false);
  let removing = $state(false);
  let removeFailed = $state(false);

  const item = $derived(catalog.item(id));
  const current = $derived(flow(id));
  const canGet = $derived(!item || item.state === "failed");
  const meta = $derived(movie ? [movie.year, runtimeText(movie.runtime)].filter(Boolean).map(String) : []);
  const tabHome = $derived(`#${router.route.tab === "home" ? "/" : `/${router.route.tab}`}`);

  async function load() {
    loadFailed = false;
    try {
      const data = await api<MovieData>(`/movies/${id}`);
      movie = data.movie;
      catalog.set(id, data.library);
      document.title = `${data.movie.title} · Cinema`;
    } catch {
      loadFailed = true;
    }
  }
  load();
  $effect(() => () => {
    document.title = "Cinema";
  });

  // Closing the sheet goes back one history entry. Two closers can fire for one
  // sheet (a confirmed pick, and the film turning up on the server), so only the
  // first one counts until the route has actually changed.
  let closing = false;
  $effect(() => {
    if (!sheet) closing = false;
  });
  function closeSheet() {
    if (!sheet || closing) return;
    closing = true;
    goBack(`#/movie/${id}`);
  }

  // The More sheet is only for a film that is not (or no longer) on the server.
  $effect(() => {
    if (sheet && movie && !canGet) untrack(closeSheet);
  });

  async function get() {
    const outcome = await getRecommended(id);
    if (outcome === "none" && router.route.movie === id && !router.route.sheet) openMore();
  }

  function openMore() {
    navigate(`#/movie/${id}/more`);
  }

  async function remove() {
    removing = true;
    removeFailed = false;
    try {
      await api(`/movies/${id}`, { method: "DELETE", timeout: 30000 });
      catalog.set(id, null);
      forgetReleases(id);
      clearMessage(id);
      confirming = false;
    } catch {
      removeFailed = true;
    }
    removing = false;
  }

  const art = (m: MovieInfo) => (m.backdrop_path ? backdropUrl(m.backdrop_path) : null);
</script>

<article class="movie" in:fade={{ duration: ms(200) }}>
  <button class="back" type="button" aria-label={t("back")} onclick={() => goBack(tabHome)}>
    <Icon name="back" size={22} stroke={2.4} />
  </button>

  {#if loadFailed}
    <div class="failed-load"><ErrorBlock retry={load} /></div>
  {:else if !movie}
    <div class="backdrop" aria-hidden="true"></div>
    <div class="body" aria-hidden="true">
      <div class="bone bone--title"></div>
      <div class="bone bone--meta"></div>
      <div class="bone bone--btn"></div>
      <div class="bone bone--btn bone--btn2"></div>
    </div>
  {:else}
    <div class="backdrop" class:bare={!movie.backdrop_path && !movie.poster_path}>
      {#if art(movie)}
        <img src={art(movie)} alt="" class:loaded={artLoaded} onload={() => (artLoaded = true)} decoding="async" />
      {:else if movie.poster_path}
        <img class="blur loaded" src={posterUrl(movie.poster_path)} alt="" />
        <img class="poster" src={posterUrl(movie.poster_path)} alt="" />
      {/if}
    </div>

    <div class="body">
      <h1 class="title">{movie.title}</h1>
      {#if meta.length}
        <p class="meta">{#each meta as part, i (i)}<span>{part}</span>{/each}</p>
      {/if}

      <div class="actions" aria-live="polite">
        {#if item}
          <div class="status-card status-card--{item.state}">
            <Status {item} />
            {#if item.state !== "failed"}
              {#if confirming}
                <div class="confirm">
                  <p>{t("remove_confirm")}</p>
                  <div class="confirm-buttons">
                    <button class="btn btn--secondary" type="button" disabled={removing} onclick={() => (confirming = false)}>{t("cancel")}</button>
                    <button class="btn btn--danger" type="button" disabled={removing} onclick={remove}>
                      {#if removing}<Spinner />{/if}{t("remove")}
                    </button>
                  </div>
                  {#if removeFailed}<p class="message">{t("error")}</p>{/if}
                </div>
              {:else}
                <button class="btn btn--quiet remove" type="button" onclick={() => (confirming = true)}>{t("remove")}</button>
              {/if}
            {/if}
          </div>
        {/if}

        {#if canGet}
          <button class="btn btn--primary btn--block btn--big" type="button" disabled={current.finding} onclick={get}>
            {#if current.finding}<Spinner />{t("finding")}{:else}<Icon name="get" size={22} stroke={2.4} />{t("recommended")}{/if}
          </button>
          <button class="btn btn--secondary btn--block" type="button" disabled={current.finding} onclick={openMore}>
            {t("more_versions")}
          </button>
          {#if current.message}
            <p class="message message--{current.message}">{t(current.message)}</p>
          {/if}
        {/if}
      </div>

      {#if movie.overview}
        <p class="overview">{movie.overview}</p>
      {/if}
    </div>

    {#if sheet && canGet}
      <MoreSheet {id} title={movie.title} onclose={closeSheet} />
    {/if}
  {/if}
</article>

<style>
  .movie { position: relative; min-height: 100dvh; }

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
    height: min(62vh, 520px);
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
  .meta { display: flex; flex-wrap: wrap; gap: 0 14px; margin: 8px 0 0; color: var(--usher); font-size: 15px; }
  .meta span + span { position: relative; }
  .meta span + span::before {
    content: "";
    position: absolute;
    left: -8px;
    top: 50%;
    width: 3px;
    height: 3px;
    border-radius: 50%;
    background: currentColor;
    opacity: 0.7;
  }

  .actions { display: flex; flex-direction: column; gap: 10px; margin-top: 22px; }
  .btn--big { min-height: 56px; font-size: 18px; }
  .status-card {
    padding: 16px;
    border-radius: var(--r-card);
    background: var(--velvet);
    box-shadow: inset 0 0 0 1px var(--line);
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .status-card--failed { background: rgb(242 154 136 / 0.07); box-shadow: inset 0 0 0 1px rgb(242 154 136 / 0.22); }
  .remove { align-self: flex-start; margin: 4px 0 -8px -8px; padding: 0 8px; }
  .confirm { margin-top: 10px; padding-top: 14px; border-top: 1px solid var(--line); }
  .confirm p { margin: 0 0 12px; font-weight: 550; }
  .confirm-buttons { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .confirm-buttons .btn { min-height: 48px; font-size: 16px; }
  .message { margin: 2px 0 0; font-size: 14px; text-align: center; color: var(--usher); }
  .message--error, .confirm .message { color: var(--failed); }
  .overview { margin: 26px 0 0; color: #d6cddd; font-size: 16px; line-height: 1.55; max-width: 62ch; }

  .failed-load { padding-top: calc(env(safe-area-inset-top) + 72px); }
  .bone { background: var(--skeleton); border-radius: 8px; }
  .bone--title { width: 70%; height: 36px; }
  .bone--meta { width: 36%; height: 16px; margin-top: 12px; }
  .bone--btn { height: 56px; margin-top: 22px; border-radius: var(--r-button); }
  .bone--btn2 { height: 52px; margin-top: 10px; }
  @media (prefers-reduced-motion: reduce) { .backdrop img { transition: none; } }
</style>
