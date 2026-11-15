<script lang="ts" module>
  // Films or shows: remembered for the session (not stored).
  let remembered: "films" | "shows" = "films";
</script>

<script lang="ts">
  import { untrack } from "svelte";
  import EmptyState from "../components/EmptyState.svelte";
  import ErrorBlock from "../components/ErrorBlock.svelte";
  import Icon from "../components/Icon.svelte";
  import PosterGrid from "../components/PosterGrid.svelte";
  import { catalog, shows } from "../lib/catalog.svelte";
  import { t } from "../lib/i18n";
  import { selectTab } from "../lib/router.svelte";

  let { active }: { active: boolean } = $props();

  let side = $state(remembered);
  function choose(next: "films" | "shows") {
    side = remembered = next;
  }

  // The server lists the newest first.
  const films = $derived(catalog.done.map((m) => ({
    id: m.tmdb_id, title: m.title, year: m.year, path: m.poster_path, state: null,
  })));
  const showItems = $derived(shows.library.map((s) => ({
    id: s.id, title: s.title, year: s.year, path: s.poster_path, state: null, href: `#/show/${s.id}`,
  })));

  // Shows are fetched each time they are looked at, like the Shows tab.
  $effect(() => {
    if (active && side === "shows") untrack(() => shows.refresh());
  });

  function findFilm() {
    selectTab("search");
    (document.getElementById("search-input") as HTMLInputElement | null)?.focus({ preventScroll: true });
  }
</script>

<div class="library" inert={!active}>
  <h1 class="screen-title">{t("tab_library")}</h1>

  <div class="switch" role="group" aria-label={t("tab_library")}>
    <button type="button" aria-pressed={side === "films"} onclick={() => choose("films")}>{t("tab_films")}</button>
    <button type="button" aria-pressed={side === "shows"} onclick={() => choose("shows")}>{t("tab_shows")}</button>
  </div>

  {#if side === "films"}
    {#if catalog.failed}
      <ErrorBlock retry={() => catalog.refresh()} />
    {:else if catalog.loaded && !films.length}
      <EmptyState icon="library" title={t("library_empty_title")} text={t("library_empty")}>
        <button class="btn btn--secondary" type="button" onclick={findFilm}>
          <Icon name="search" size={20} />{t("find_film")}
        </button>
      </EmptyState>
    {:else if catalog.loaded}
      <PosterGrid items={films} />
    {/if}
  {:else if shows.failed || (shows.loaded && shows.sonarrError)}
    <ErrorBlock retry={() => shows.refresh()} />
  {:else if shows.loaded && !showItems.length}
    <EmptyState icon="shows" title={t("library_shows_empty_title")} text={t("library_shows_empty")}>
      <button class="btn btn--secondary" type="button" onclick={() => selectTab("shows")}>
        <Icon name="shows" size={20} />{t("find_show")}
      </button>
    </EmptyState>
  {:else if shows.loaded}
    <PosterGrid items={showItems} />
  {/if}
</div>

<style>
  .library { padding-bottom: 12px; }

  /* A segmented control, like iOS: the chosen side is a raised pill. */
  .switch {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 2px;
    margin: 0 var(--gutter) 20px;
    padding: 3px;
    border-radius: 14px;
    background: var(--velvet);
    box-shadow: inset 0 0 0 1px var(--line);
  }
  .switch button {
    min-height: 44px;
    border: 0;
    border-radius: 11px;
    background: transparent;
    color: var(--usher);
    font-size: 15px;
    font-weight: 600;
    cursor: pointer;
    transition: background-color 0.2s ease, color 0.2s ease;
  }
  .switch button[aria-pressed="true"] {
    background: var(--aisle);
    color: var(--screen);
    box-shadow: 0 1px 6px rgb(0 0 0 / 0.35), inset 0 0 0 1px rgb(255 255 255 / 0.06);
  }
  .switch button:active { color: var(--screen); }
  @media (prefers-reduced-motion: reduce) { .switch button { transition: none; } }
</style>
