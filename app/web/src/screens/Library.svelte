<script lang="ts">
  import EmptyState from "../components/EmptyState.svelte";
  import ErrorBlock from "../components/ErrorBlock.svelte";
  import Icon from "../components/Icon.svelte";
  import PosterGrid from "../components/PosterGrid.svelte";
  import { catalog } from "../lib/catalog.svelte";
  import { t } from "../lib/i18n";
  import { selectTab } from "../lib/router.svelte";

  let { active }: { active: boolean } = $props();

  // The server lists the newest first.
  const items = $derived(catalog.done.map((m) => ({
    id: m.tmdb_id, title: m.title, year: m.year, path: m.poster_path, state: null,
  })));

  function findFilm() {
    selectTab("search");
    (document.getElementById("search-input") as HTMLInputElement | null)?.focus({ preventScroll: true });
  }
</script>

<div class="library" inert={!active}>
  <h1 class="screen-title">{t("tab_library")}</h1>
  {#if catalog.failed}
    <ErrorBlock retry={() => catalog.refresh()} />
  {:else if catalog.loaded && !items.length}
    <EmptyState icon="library" title={t("library_empty_title")} text={t("library_empty")}>
      <button class="btn btn--secondary" type="button" onclick={findFilm}>
        <Icon name="search" size={20} />{t("find_film")}
      </button>
    </EmptyState>
  {:else if catalog.loaded}
    <PosterGrid {items} />
  {/if}
</div>

<style>
  .library { padding-bottom: 12px; }
</style>
