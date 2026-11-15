<script lang="ts">
  import { untrack } from "svelte";
  import EmptyState from "../components/EmptyState.svelte";
  import ErrorBlock from "../components/ErrorBlock.svelte";
  import Icon from "../components/Icon.svelte";
  import PersonRow from "../components/PersonRow.svelte";
  import PosterGrid from "../components/PosterGrid.svelte";
  import Spinner from "../components/Spinner.svelte";
  import TileRow from "../components/TileRow.svelte";
  import { api, ApiError, type Card, type Genre, type SearchData } from "../lib/api";
  import { catalog } from "../lib/catalog.svelte";
  import { t } from "../lib/i18n";
  import { navigate, rememberQuery, router } from "../lib/router.svelte";

  let { active }: { active: boolean } = $props();

  const DELAY_MS = 350;

  let input: HTMLInputElement | undefined = $state();
  let text = $state(router.route.q);
  let results = $state<SearchData | null>(null);
  let resultsFor = $state("");
  let loading = $state(false);
  let failed = $state(false);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let request: AbortController | null = null;

  const query = $derived(text.trim());
  const cards = (list: Card[]) =>
    list.map((c) => ({ id: c.id, title: c.title, year: c.year, path: c.poster_path, state: catalog.state(c) }));
  const nothingFound = $derived(
    !!results && !results.films.length && !results.people.length && !results.studios.length && !results.collections.length);

  // Genre chips for before you type. Loaded once; a failure just leaves them out
  // until the tab is opened again or the field is cleared.
  let genres = $state<Genre[] | null>(null);
  let genresLoading = false;
  async function loadGenres() {
    if (genresLoading) return;
    genresLoading = true;
    try {
      genres = (await api<{ genres: Genre[] }>("/genres")).genres;
    } catch {
      // Popular right now still shows; the chips come back next time.
    }
    genresLoading = false;
  }
  $effect(() => {
    if (active && !query && !genres) untrack(loadGenres);
  });

  async function run(q: string) {
    request?.abort();
    if (!q) {
      results = null;
      resultsFor = "";
      loading = false;
      failed = false;
      return;
    }
    const mine = (request = new AbortController());
    loading = true;
    failed = false;
    try {
      results = await api<SearchData>(`/search?q=${encodeURIComponent(q)}`, { signal: mine.signal });
      resultsFor = q;
    } catch (err) {
      if (err instanceof ApiError && err.status === -1) return; // superseded
      failed = true;
    }
    if (request === mine) loading = false;
  }

  function sync(q: string) {
    rememberQuery(q);
    const href = q ? `#/search?q=${encodeURIComponent(q)}` : "#/search";
    if (active && location.hash !== href) navigate(href, { replace: true });
  }

  // Typing updates the URL (debounced); the URL drives the search.
  function onInput() {
    clearTimeout(timer);
    timer = setTimeout(() => sync(query), DELAY_MS);
  }

  function submit(event: SubmitEvent) {
    event.preventDefault();
    clearTimeout(timer);
    if (failed && query === router.route.q.trim()) run(query);
    else sync(query);
    input?.blur(); // put the keyboard away to show the results
  }

  function clear() {
    clearTimeout(timer);
    text = "";
    sync("");
    input?.focus();
  }

  $effect(() => {
    const q = router.route.q.trim();
    if (!active) return;
    untrack(() => {
      if (q !== query) {
        // The URL changed under us (back button): show its query.
        clearTimeout(timer);
        text = q;
      }
      if (q !== resultsFor || (!q && results)) run(q);
      rememberQuery(q);
    });
  });

  // Opening the tab puts the cursor in the field (the tab bar also focuses it inside
  // the tap, which is what raises the iOS keyboard).
  $effect(() => {
    if (active && !text) input?.focus({ preventScroll: true });
  });
</script>

<div class="search" inert={!active}>
  <h1 class="screen-title">{t("tab_search")}</h1>
  <form class="field-wrap" role="search" onsubmit={submit}>
    <label class="field">
      <Icon name="search" size={20} />
      <span class="sr-only">{t("search")}</span>
      <input
        id="search-input"
        bind:this={input}
        bind:value={text}
        oninput={onInput}
        type="search"
        placeholder={t("search")}
        enterkeyhint="search"
        autocomplete="off"
        autocapitalize="off"
        spellcheck="false"
      />
      {#if loading}
        <span class="trail"><Spinner size={16} /></span>
      {:else if text}
        <button class="trail clear" type="button" aria-label={t("clear")} onclick={clear}>
          <Icon name="clear" size={20} stroke={1.8} />
        </button>
      {/if}
    </label>
  </form>

  {#if failed}
    <ErrorBlock retry={() => run(query)} />
  {:else if query && results && resultsFor}
    {#if nothingFound}
      <EmptyState icon="search" title={t("no_results")} text={t("no_results_hint")} />
    {:else}
      <div class="results">
        {#if results.films.length}
          <section class="films">
            <h2 class="section-title">{t("films")}</h2>
            <PosterGrid items={cards(results.films)} />
          </section>
        {/if}
        {#if results.people.length}
          <PersonRow people={results.people} />
        {/if}
        {#if results.studios.length}
          <TileRow title={t("studios")} variant="logo"
                   tiles={results.studios.map((s) => ({ id: s.id, name: s.name, href: `#/studio/${s.id}`, path: s.logo_path }))} />
        {/if}
        {#if results.collections.length}
          <TileRow title={t("collections")} variant="poster"
                   tiles={results.collections.map((c) => ({ id: c.id, name: c.name, href: `#/collection/${c.id}`, path: c.poster_path }))} />
        {/if}
      </div>
    {/if}
  {:else if !query}
    {#if genres?.length}
      <section class="genres">
        <h2 class="section-title">{t("genres")}</h2>
        <ul class="chips">
          {#each genres as genre (genre.id)}
            <li><a class="chip" href="#/genre/{genre.id}">{genre.name}</a></li>
          {/each}
        </ul>
      </section>
    {/if}
    {#if catalog.popular.length}
      <h2 class="section-title">{t("popular_now")}</h2>
      <PosterGrid items={cards(catalog.popular)} />
    {:else if catalog.failed || catalog.catalogError}
      <ErrorBlock retry={() => catalog.refresh()} />
    {/if}
  {/if}
</div>

<style>
  .search { padding-bottom: 12px; }
  .field-wrap {
    position: sticky;
    top: 0;
    z-index: 5;
    padding: calc(env(safe-area-inset-top) + 8px) var(--gutter) 12px;
    margin-top: calc(-1 * env(safe-area-inset-top) - 8px);
    background: linear-gradient(180deg, var(--curtain) 70%, rgb(21 17 27 / 0));
  }
  .field {
    display: flex;
    align-items: center;
    gap: 10px;
    height: 48px;
    padding: 0 2px 0 14px;
    border-radius: 14px;
    background: var(--aisle);
    color: var(--usher);
    box-shadow: inset 0 0 0 1px var(--line);
  }
  .field:focus-within { box-shadow: inset 0 0 0 1.5px rgb(243 194 107 / 0.55); }
  input {
    flex: 1;
    min-width: 0;
    height: 100%;
    border: 0;
    outline: 0;
    background: transparent;
    color: var(--screen);
    font-size: 17px; /* 16px+ keeps iOS from zooming in */
    -webkit-appearance: none;
    appearance: none;
  }
  input::placeholder { color: var(--usher); opacity: 1; }
  input::-webkit-search-cancel-button { display: none; }
  .trail { width: 44px; height: 44px; display: grid; place-items: center; flex: none; }
  .clear { border: 0; background: none; padding: 0; color: var(--usher); cursor: pointer; border-radius: 50%; }
  .clear:active { background: rgb(255 255 255 / 0.08); }
  .section-title { margin-top: 10px; }
  /* Whichever group comes first sits right under the field. */
  .results > :global(:first-child) { margin-top: 10px; }
  .results > .films:first-child { margin-top: 0; }
  .genres { margin-bottom: 28px; }
  .chips {
    list-style: none;
    margin: 0;
    padding: 0 var(--gutter);
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }
  .chip {
    display: inline-flex;
    align-items: center;
    min-height: 44px;
    padding: 0 16px;
    border-radius: 999px;
    background: var(--velvet);
    box-shadow: inset 0 0 0 1px var(--line);
    font-size: 15px;
    font-weight: 550;
    -webkit-touch-callout: none;
    transition: transform 0.15s ease, background-color 0.2s ease;
  }
  .chip:active { transform: scale(0.96); background: var(--aisle); }
  @media (prefers-reduced-motion: reduce) { .chip { transition: none; } }
</style>
