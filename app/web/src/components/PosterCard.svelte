<script lang="ts">
  import type { State } from "../lib/api";
  import { t } from "../lib/i18n";
  import Icon from "./Icon.svelte";
  import Poster from "./Poster.svelte";

  // A film by default; a show passes its own href (#/show/{id}).
  let { id, title, year, path, state = null, caption = false, eager = false, href = `#/movie/${id}` }: {
    id: number; title: string; year: number | null; path: string | null;
    state?: State | null; caption?: boolean; eager?: boolean; href?: string;
  } = $props();

  const label = $derived(
    [year ? `${title} (${year})` : title,
     state === "done" ? t("done") : state === "preparing" || state === "ready" ? t("downloads_active") : null]
      .filter(Boolean).join(", "));
</script>

<a class="card" {href} aria-label={label}>
  <div class="art">
    <Poster {path} {title} {year} {eager} />
    {#if state === "done"}
      <span class="badge badge--done" title={t("done")}><Icon name="check" size={13} stroke={3} /></span>
    {:else if state === "preparing" || state === "ready"}
      <span class="badge badge--active" title={t("downloads_active")}><Icon name="get" size={13} stroke={2.6} /></span>
    {/if}
  </div>
  {#if caption}
    <span class="caption" aria-hidden="true">
      <span class="caption-title">{title}</span>
      {#if year}<span class="caption-year">{year}</span>{/if}
    </span>
  {/if}
</a>

<style>
  .card {
    display: flex;
    flex-direction: column;
    gap: 8px;
    min-width: 0;
    -webkit-touch-callout: none;
  }
  .art {
    position: relative;
    transition: transform 0.18s ease;
  }
  .card:active .art { transform: scale(0.965); }
  .badge {
    position: absolute;
    top: 6px;
    right: 6px;
    width: 22px;
    height: 22px;
    display: grid;
    place-items: center;
    border-radius: 50%;
    box-shadow: 0 2px 8px rgb(0 0 0 / 0.45);
  }
  .badge--done { background: var(--tungsten); color: var(--tungsten-ink); }
  .badge--active { background: rgb(20 16 26 / 0.78); color: var(--tungsten); backdrop-filter: blur(6px); -webkit-backdrop-filter: blur(6px); }
  .caption { display: flex; flex-direction: column; gap: 1px; padding: 0 2px; }
  .caption-title {
    font-size: 13px;
    line-height: 1.25;
    font-weight: 550;
    color: var(--screen);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .caption-year { font-size: 12px; color: var(--usher); font-variant-numeric: tabular-nums; }
  @media (prefers-reduced-motion: reduce) { .art { transition: none; } }
</style>
