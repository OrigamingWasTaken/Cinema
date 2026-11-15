<script lang="ts">
  import { fade } from "svelte/transition";
  import Avatar from "../components/Avatar.svelte";
  import EmptyState from "../components/EmptyState.svelte";
  import ErrorBlock from "../components/ErrorBlock.svelte";
  import Icon from "../components/Icon.svelte";
  import PosterGrid from "../components/PosterGrid.svelte";
  import Spinner from "../components/Spinner.svelte";
  import StudioLogo from "../components/StudioLogo.svelte";
  import {
    api, ApiError, backdropUrl, type Card, type CollectionRef, type FilmPage, type Genre,
    type PersonRef, type StudioRef,
  } from "../lib/api";
  import { catalog } from "../lib/catalog.svelte";
  import { t } from "../lib/i18n";
  import { ms } from "../lib/motion";
  import { goBack, router, tabHref, type BrowseKind } from "../lib/router.svelte";

  // A page of films pushed over the current tab: a person's films, a studio's, a
  // collection's or a genre's. It stays mounted under a film opened from it, so
  // coming back keeps what was loaded and where you were.
  let { kind, id, active }: { kind: BrowseKind; id: number; active: boolean } = $props();

  type Response =
    | { person: PersonRef; films: Card[] }
    | ({ studio: StudioRef } & FilmPage)
    | { collection: CollectionRef; films: Card[] }
    | ({ genre: Genre } & FilmPage);

  interface Header { name: string; image: string | null; backdrop: string | null }

  const ENDPOINTS: Record<BrowseKind, string> = {
    person: "/people", studio: "/studios", collection: "/collections", genre: "/genres",
  };

  let header = $state<Header | null>(null);
  let films = $state<Card[]>([]);
  let page = $state(1);
  let totalPages = $state(1);
  let loadFailed = $state(false);
  let notFound = $state(false);
  let more = $state<"idle" | "loading" | "failed">("idle");
  let backdropLoaded = $state(false);

  const paged = $derived(kind === "studio" || kind === "genre");
  const items = $derived(films.map((c) => ({
    id: c.id, title: c.title, year: c.year, path: c.poster_path, state: catalog.state(c),
  })));

  async function fetchPage(n: number) {
    const query = kind === "studio" || kind === "genre" ? `?page=${n}` : "";
    const data = await api<Response>(`${ENDPOINTS[kind]}/${id}${query}`);
    let head: Header;
    if ("person" in data) head = { name: data.person.name, image: data.person.profile_path, backdrop: null };
    else if ("studio" in data) head = { name: data.studio.name, image: data.studio.logo_path, backdrop: null };
    else if ("collection" in data) head = {
      name: data.collection.name, image: data.collection.poster_path, backdrop: data.collection.backdrop_path ?? null,
    };
    else head = { name: data.genre.name, image: null, backdrop: null };
    const pages = "page" in data ? { page: data.page, total: data.total_pages } : { page: 1, total: 1 };
    return { head, films: data.films, ...pages };
  }

  async function load() {
    loadFailed = false;
    notFound = false;
    try {
      const first = await fetchPage(1);
      header = first.head;
      films = dedupe([], first.films);
      page = first.page;
      totalPages = first.total;
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) notFound = true;
      else loadFailed = true;
    }
  }
  load();

  /** `next` added after `list`, without the films already in it. */
  function dedupe(list: Card[], next: Card[]): Card[] {
    const seen = new Set(list.map((c) => c.id));
    return [...list, ...next.filter((c) => !seen.has(c.id) && seen.add(c.id))];
  }

  async function loadMore() {
    if (more === "loading") return;
    more = "loading";
    try {
      const next = await fetchPage(page + 1);
      films = dedupe(films, next.films);
      page = next.page;
      totalPages = next.total;
      more = "idle";
    } catch {
      more = "failed";
    }
  }

  $effect(() => {
    if (active && header) document.title = `${header.name} · Cinema`;
  });
  $effect(() => () => {
    document.title = "Cinema";
  });
</script>

<article class="browse browse--{kind}" inert={!active} in:fade={{ duration: ms(200) }}>
  <button class="back" type="button" aria-label={t("back")} onclick={() => goBack(tabHref(router.route.tab))}>
    <Icon name="back" size={22} stroke={2.4} />
  </button>

  {#if notFound}
    <div class="pad-top"><EmptyState icon="search" title={t("browse_empty")} /></div>
  {:else if loadFailed}
    <div class="pad-top"><ErrorBlock retry={load} /></div>
  {:else if !header}
    <div class="skeleton" aria-hidden="true">
      {#if kind === "collection"}
        <div class="backdrop"></div>
      {:else}
        <div class="head">
          {#if kind === "person"}<div class="bone bone--photo"></div>{/if}
          {#if kind === "studio"}<div class="bone bone--logo"></div>{/if}
          <div class="bone bone--name"></div>
        </div>
      {/if}
      <div class="bone-grid">{#each Array(6) as _, i (i)}<div class="bone bone--poster"></div>{/each}</div>
    </div>
  {:else}
    {#if kind === "person"}
      <header class="head">
        <Avatar path={header.image} name={header.name} size={112} large />
        <h1 class="name">{header.name}</h1>
      </header>
      <h2 class="section-title">{t("films_with", { name: header.name })}</h2>
    {:else if kind === "studio"}
      <header class="head">
        {#if header.image}
          <div class="logo"><StudioLogo path={header.image} name={header.name} /></div>
        {/if}
        <h1 class="name" class:name--small={!!header.image}>{header.name}</h1>
      </header>
    {:else if kind === "collection"}
      <header class="backdrop" class:bare={!header.backdrop}>
        {#if header.backdrop}
          <img src={backdropUrl(header.backdrop, "w1280")} alt="" class:loaded={backdropLoaded}
               onload={() => (backdropLoaded = true)} decoding="async" />
        {/if}
        <h1 class="name name--over">{header.name}</h1>
      </header>
    {:else}
      <header class="head head--genre">
        <h1 class="name">{header.name}</h1>
      </header>
    {/if}

    {#if items.length}
      <PosterGrid {items} />
    {:else}
      <EmptyState icon="library" title={t("browse_empty")} />
    {/if}

    {#if paged && page < totalPages}
      <div class="more" aria-live="polite">
        {#if more === "failed"}
          <ErrorBlock retry={loadMore} />
        {:else}
          <button class="btn btn--secondary btn--block" type="button" disabled={more === "loading"} onclick={loadMore}>
            {#if more === "loading"}<Spinner />{/if}{t("load_more")}
          </button>
        {/if}
      </div>
    {/if}
  {/if}
</article>

<style>
  .browse { position: relative; min-height: 100dvh; padding-bottom: 12px; }

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

  .pad-top { padding-top: calc(env(safe-area-inset-top) + 64px); }

  /* Person and studio: centred, like a profile. */
  .head {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 14px;
    padding: calc(env(safe-area-inset-top) + 64px) var(--gutter) 26px;
    text-align: center;
    background: radial-gradient(70% 60% at 50% 0%, rgb(243 194 107 / 0.08), transparent 70%);
  }
  .name {
    margin: 0;
    font: 650 clamp(28px, 8vw, 36px)/1.08 var(--serif);
    letter-spacing: -0.01em;
    text-wrap: balance;
  }
  .name--small { font-size: 22px; font-weight: 600; }
  .logo { width: min(220px, 60vw); }
  .logo :global(.plate) { box-shadow: 0 12px 34px rgb(0 0 0 / 0.4); }

  /* Genre: a large title, left-aligned like a tab's. */
  .head--genre {
    align-items: flex-start;
    padding-top: calc(env(safe-area-inset-top) + 70px);
    padding-bottom: 18px;
    text-align: left;
    background: none;
  }
  .head--genre .name { font-size: 34px; }

  /* Collection: its backdrop, with the name over it. */
  .backdrop {
    position: relative;
    height: min(46vh, 380px);
    margin-bottom: 18px;
    overflow: hidden;
    display: flex;
    align-items: flex-end;
    background: linear-gradient(180deg, #2a2133, var(--curtain));
  }
  .backdrop.bare {
    height: calc(env(safe-area-inset-top) + 180px);
    background: radial-gradient(90% 70% at 50% 0%, rgb(243 194 107 / 0.1), transparent 70%), linear-gradient(180deg, #2a2133, var(--curtain));
  }
  .backdrop img {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: 50% 25%;
    opacity: 0;
    transition: opacity 0.6s ease;
  }
  .backdrop img.loaded { opacity: 1; }
  .backdrop::after {
    content: "";
    position: absolute;
    inset: 0;
    background:
      linear-gradient(180deg, rgb(21 17 27 / 0.5) 0%, transparent 24%),
      linear-gradient(0deg, var(--curtain) 0%, rgb(21 17 27 / 0.65) 26%, transparent 64%);
  }
  .name--over {
    position: relative;
    z-index: 1;
    padding: 0 var(--gutter) 4px;
    text-shadow: 0 2px 24px rgb(0 0 0 / 0.55);
  }

  .section-title { margin-top: 0; }
  .more { margin: 26px var(--gutter) 8px; }
  .more :global(.error) { width: 100%; margin: 0; }

  .skeleton .head { background: none; }
  .bone { background: var(--skeleton); border-radius: 8px; }
  .bone--photo { width: 112px; height: 112px; border-radius: 50%; }
  .bone--logo { width: min(220px, 60vw); aspect-ratio: 7 / 4; }
  .bone--name { width: 52%; height: 32px; }
  .bone-grid {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 18px 10px;
    padding: 0 var(--gutter);
  }
  .bone--poster { aspect-ratio: 2 / 3; border-radius: var(--r-poster); }
  .browse--genre .skeleton .head { align-items: flex-start; padding-top: calc(env(safe-area-inset-top) + 70px); }

  @media (prefers-reduced-motion: reduce) { .backdrop img { transition: none; } }
</style>
