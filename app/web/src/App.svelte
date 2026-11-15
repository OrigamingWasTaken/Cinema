<script lang="ts">
  import Icon from "./components/Icon.svelte";
  import TabBar from "./components/TabBar.svelte";
  import { catalog } from "./lib/catalog.svelte";
  import { t } from "./lib/i18n";
  import { goBack, onTab as isOnTab, router, tabHref, TABS, type Tab } from "./lib/router.svelte";
  import { ui } from "./lib/ui.svelte";
  import Browse from "./screens/Browse.svelte";
  import Downloads from "./screens/Downloads.svelte";
  import Home from "./screens/Home.svelte";
  import Library from "./screens/Library.svelte";
  import Movie from "./screens/Movie.svelte";
  import Search from "./screens/Search.svelte";
  import Shows from "./screens/Shows.svelte";

  const SCREENS = { films: Home, shows: Shows, search: Search, library: Library, downloads: Downloads };

  // A tab is mounted the first time it is opened and then kept (hidden), so it keeps
  // its state; the router keeps its scroll position. A browse page is kept the same
  // way under a film opened from it.
  const visited = $state<Record<Tab, boolean>>({
    films: false, shows: false, search: false, library: false, downloads: false,
  });
  $effect.pre(() => {
    visited[router.route.tab] = true;
  });

  const browse = $derived(router.route.browse);
  const onTab = $derived(isOnTab(router.route));

  catalog.start();
</script>

<main inert={ui.sheetOpen}>
  {#each TABS as tab (tab)}
    {#if visited[tab]}
      {@const Screen = SCREENS[tab]}
      <div class="view" hidden={!onTab || router.route.tab !== tab}>
        <Screen active={onTab && router.route.tab === tab} />
      </div>
    {/if}
  {/each}

  {#if browse}
    {#key `${browse.kind}/${browse.id}`}
      <div class="view" hidden={router.route.movie != null || router.route.show != null}>
        <Browse kind={browse.kind} id={browse.id} active={router.route.movie == null && router.route.show == null} />
      </div>
    {/key}
  {/if}

  {#if router.route.movie != null}
    {#key router.route.movie}
      <Movie id={router.route.movie} sheet={router.route.sheet} />
    {/key}
  {:else if router.route.show != null}
    <!-- A placeholder until the show page exists: just the way back. -->
    <article class="show-page">
      <button class="back" type="button" aria-label={t("back")} onclick={() => goBack(tabHref(router.route.tab))}>
        <Icon name="back" size={22} stroke={2.4} />
      </button>
    </article>
  {/if}
</main>

<TabBar />

<style>
  main {
    min-height: 100dvh;
    padding-bottom: var(--tabbar-space);
  }
  .show-page { min-height: 100dvh; }
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
</style>
