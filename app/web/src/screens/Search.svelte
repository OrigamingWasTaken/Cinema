<script lang="ts">
  import { untrack } from "svelte";
  import EmptyState from "../components/EmptyState.svelte";
  import ErrorBlock from "../components/ErrorBlock.svelte";
  import Icon from "../components/Icon.svelte";
  import PosterGrid from "../components/PosterGrid.svelte";
  import Spinner from "../components/Spinner.svelte";
  import { api, ApiError, type Card } from "../lib/api";
  import { catalog } from "../lib/catalog.svelte";
  import { t } from "../lib/i18n";
  import { navigate, rememberQuery, router } from "../lib/router.svelte";

  let { active }: { active: boolean } = $props();

  const DELAY_MS = 350;

  let input: HTMLInputElement | undefined = $state();
  let text = $state(router.route.q);
  let results = $state<Card[] | null>(null);
  let resultsFor = $state("");
  let loading = $state(false);
  let failed = $state(false);
  let timer: ReturnType<typeof setTimeout> | undefined;
  let request: AbortController | null = null;

  const query = $derived(text.trim());
  const cards = (list: Card[]) =>
    list.map((c) => ({ id: c.id, title: c.title, year: c.year, path: c.poster_path, state: catalog.state(c) }));

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
      const data = await api<{ results: Card[] }>(`/search?q=${encodeURIComponent(q)}`, { signal: mine.signal });
      results = data.results;
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
    {#if results.length}
      <PosterGrid items={cards(results)} />
    {:else}
      <EmptyState title={t("no_results")} text={t("no_results_hint")} />
    {/if}
  {:else if !query}
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
    padding: 0 6px 0 14px;
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
  .trail { width: 40px; height: 40px; display: grid; place-items: center; flex: none; }
  .clear { border: 0; background: none; padding: 0; color: var(--usher); cursor: pointer; border-radius: 50%; }
  .clear:active { background: rgb(255 255 255 / 0.08); }
  .section-title { margin-top: 10px; }
</style>
