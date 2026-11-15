<script lang="ts">
  import EmptyState from "../components/EmptyState.svelte";
  import ErrorBlock from "../components/ErrorBlock.svelte";
  import Icon from "../components/Icon.svelte";
  import Poster from "../components/Poster.svelte";
  import Status from "../components/Status.svelte";
  import { catalog } from "../lib/catalog.svelte";
  import { formatNumber, t } from "../lib/i18n";

  let { active }: { active: boolean } = $props();

  const used = $derived(catalog.disk && catalog.disk.total_bytes
    ? 1 - catalog.disk.free_bytes / catalog.disk.total_bytes : null);
</script>

<div class="downloads" inert={!active}>
  <h1 class="screen-title">{t("tab_downloads")}</h1>

  {#if catalog.failed}
    <ErrorBlock retry={() => catalog.refresh()} />
  {:else if catalog.loaded}
    {#if !catalog.active.length && !catalog.broken.length}
      <EmptyState icon="downloads" title={t("downloads_empty_title")} text={t("downloads_empty")} />
    {/if}

    {#if catalog.active.length}
      <section class="group">
        <h2 class="section-title">{t("downloads_active")}</h2>
        <ul class="list">
          {#each catalog.active as item (item.tmdb_id)}
            <li>
              <a class="row" href="#/movie/{item.tmdb_id}">
                <span class="thumb"><Poster path={item.poster_path} title={item.title} year={item.year} /></span>
                <span class="body">
                  <span class="title">{item.title}</span>
                  <Status {item} compact />
                </span>
              </a>
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    {#if catalog.broken.length}
      <section class="group">
        <h2 class="section-title">{t("downloads_failed")}</h2>
        <ul class="list">
          {#each catalog.broken as item (item.tmdb_id)}
            <li>
              <a class="row row--failed" href="#/movie/{item.tmdb_id}/more">
                <span class="thumb"><Poster path={item.poster_path} title={item.title} year={item.year} /></span>
                <span class="body">
                  <span class="title">{item.title}</span>
                  <span class="retry">{t("try_another")}<Icon name="chevron" size={16} stroke={2.4} /></span>
                </span>
              </a>
            </li>
          {/each}
        </ul>
      </section>
    {/if}

    {#if catalog.disk}
      <footer class="disk">
        {#if used != null}
          <div class="disk-bar" aria-hidden="true"><div class="disk-used" style:width="{Math.round(used * 100)}%"></div></div>
        {/if}
        <p>{t("free_space", { n: formatNumber(Math.round(catalog.disk.free_bytes / 1e9)) })}</p>
      </footer>
    {/if}
  {/if}
</div>

<style>
  .downloads { padding-bottom: 12px; }
  .group { margin-top: 6px; }
  .group + .group { margin-top: 26px; }
  .list { list-style: none; margin: 0; padding: 0 var(--gutter); display: flex; flex-direction: column; gap: 10px; }
  .row {
    display: flex;
    gap: 14px;
    align-items: center;
    padding: 10px;
    border-radius: var(--r-card);
    background: var(--velvet);
    box-shadow: inset 0 0 0 1px var(--line);
    transition: background-color 0.15s ease;
  }
  .row:active { background: var(--aisle); }
  .thumb { width: 64px; flex: none; }
  .body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 6px; }
  .title {
    font: 600 17px/1.25 var(--serif);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .retry {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: var(--tungsten);
    font-size: 14px;
    font-weight: 600;
    min-height: 24px;
  }
  .row--failed .thumb { opacity: 0.6; filter: grayscale(0.6); }
  .disk {
    margin: 36px var(--gutter) 0;
    display: flex;
    flex-direction: column;
    gap: 8px;
    align-items: center;
    color: var(--usher);
    font-size: 13px;
  }
  .disk p { margin: 0; }
  .disk-bar { width: 120px; height: 4px; border-radius: 2px; background: rgb(255 255 255 / 0.1); overflow: hidden; }
  .disk-used { height: 100%; background: var(--usher); opacity: 0.6; }
  @media (prefers-reduced-motion: reduce) { .row { transition: none; } }
</style>
