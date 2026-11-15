<script lang="ts">
  import { catalog } from "../lib/catalog.svelte";
  import { t, type StringKey } from "../lib/i18n";
  import { router, selectTab, tabHref, TABS, type Tab } from "../lib/router.svelte";
  import { ui } from "../lib/ui.svelte";
  import Icon from "./Icon.svelte";

  const LABELS: Record<Tab, StringKey> = {
    home: "tab_home", search: "tab_search", library: "tab_library", downloads: "tab_downloads",
  };

  function tap(event: MouseEvent, tab: Tab) {
    event.preventDefault();
    selectTab(tab);
    // Focus inside the tap itself: iOS only raises the keyboard from a user gesture.
    if (tab === "search" && router.route.movie == null && router.route.browse == null) {
      const input = document.getElementById("search-input") as HTMLInputElement | null;
      if (input && !input.value) input.focus({ preventScroll: true });
    }
  }
</script>

<nav class="tabbar" aria-label="Cinema" inert={ui.sheetOpen}>
  {#each TABS as tab (tab)}
    {@const current = router.route.tab === tab}
    <a class="tab" class:current href={tabHref(tab)} aria-current={current ? "page" : undefined}
       onclick={(event) => tap(event, tab)}>
      <span class="glyph">
        <Icon name={tab} size={24} stroke={current ? 2.3 : 1.8} />
        {#if tab === "downloads" && catalog.active.length}
          <span class="count" aria-hidden="true">{catalog.active.length}</span>
        {/if}
      </span>
      <span class="label">
        {t(LABELS[tab])}
        {#if tab === "downloads" && catalog.active.length}
          <span class="sr-only">({catalog.active.length})</span>
        {/if}
      </span>
    </a>
  {/each}
</nav>

<style>
  .tabbar {
    position: fixed;
    z-index: 30;
    left: 50%;
    bottom: calc(env(safe-area-inset-bottom) + 10px);
    transform: translateX(-50%);
    width: min(calc(100% - 24px), 440px);
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    padding: 6px;
    border-radius: 26px;
    background: rgb(32 26 40 / 0.72);
    -webkit-backdrop-filter: blur(22px) saturate(160%);
    backdrop-filter: blur(22px) saturate(160%);
    box-shadow:
      inset 0 0 0 1px rgb(255 255 255 / 0.08),
      0 12px 32px rgb(0 0 0 / 0.5);
  }
  .tab {
    position: relative;
    min-height: 52px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 3px;
    border-radius: 20px;
    color: var(--usher);
    transition: color 0.2s ease, background-color 0.2s ease;
  }
  .tab.current { color: var(--tungsten); background: rgb(243 194 107 / 0.1); }
  .tab:active { background: rgb(255 255 255 / 0.06); }
  .glyph { position: relative; }
  .label {
    font-size: 10.5px;
    font-weight: 600;
    letter-spacing: 0.01em;
    white-space: nowrap;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    padding: 0 2px;
  }
  .count {
    position: absolute;
    top: -5px;
    right: -10px;
    min-width: 18px;
    height: 18px;
    padding: 0 5px;
    border-radius: 9px;
    background: var(--tungsten);
    color: var(--tungsten-ink);
    font-size: 11px;
    font-weight: 700;
    line-height: 18px;
    text-align: center;
    box-shadow: 0 0 0 2px rgb(32 26 40);
  }
  @media (prefers-reduced-motion: reduce) { .tab { transition: none; } }
</style>
