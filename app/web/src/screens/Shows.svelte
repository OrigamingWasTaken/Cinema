<script lang="ts">
  import { untrack } from "svelte";
  import ErrorBlock from "../components/ErrorBlock.svelte";
  import PosterRow from "../components/PosterRow.svelte";
  import { shows } from "../lib/catalog.svelte";
  import { t } from "../lib/i18n";

  let { active }: { active: boolean } = $props();

  // Fetched each time the tab is opened, so a show just followed or got turns up in
  // "Your shows"; the rows already loaded stay up meanwhile.
  $effect(() => {
    if (active) untrack(() => shows.refresh());
  });

  const yours = $derived(shows.sonarrError ? [] : shows.yours);
</script>

<div class="shows" inert={!active}>
  <h1 class="screen-title">{t("tab_shows")}</h1>

  {#if shows.failed}
    <ErrorBlock retry={() => shows.refresh()} />
  {:else if !shows.loaded}
    {#each [0, 1] as row (row)}
      <div class="bone-row" aria-hidden="true">
        <div class="bone bone--heading"></div>
        <div class="bone-posters">{#each [0, 1, 2, 3] as p (p)}<div class="bone bone--poster"></div>{/each}</div>
      </div>
    {/each}
  {:else}
    <div class="rows">
      {#if yours.length}
        <PosterRow title={t("your_shows")} cards={yours} kind="show" />
      {/if}
      {#if shows.trending.length}
        <PosterRow title={t("trending")} cards={shows.trending} kind="show" />
      {/if}
      {#if shows.popular.length}
        <PosterRow title={t("popular")} cards={shows.popular} kind="show" />
      {/if}
    </div>
    {#if shows.catalogError}
      <ErrorBlock retry={() => shows.refresh()} />
    {/if}
  {/if}
</div>

<style>
  .shows { padding-bottom: 12px; }
  /* The first row sits right under the title. */
  .rows > :global(.row:first-child) { margin-top: 6px; }

  .bone { background: var(--skeleton); border-radius: 8px; }
  .bone-row { margin-top: 6px; margin-bottom: 28px; }
  .bone--heading { width: 46%; height: 20px; margin: 0 var(--gutter) 12px; }
  .bone-posters { display: grid; grid-template-columns: repeat(4, 31%); gap: 10px; padding: 0 var(--gutter); overflow: hidden; }
  .bone--poster { aspect-ratio: 2 / 3; border-radius: var(--r-poster); }
</style>
