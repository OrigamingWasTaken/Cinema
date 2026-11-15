<script lang="ts">
  import TabBar from "./components/TabBar.svelte";
  import { catalog } from "./lib/catalog.svelte";
  import { onTab as isOnTab, playHref, router, TABS, type Tab } from "./lib/router.svelte";
  import { ui } from "./lib/ui.svelte";
  import Browse from "./screens/Browse.svelte";
  import Downloads from "./screens/Downloads.svelte";
  import Home from "./screens/Home.svelte";
  import Library from "./screens/Library.svelte";
  import Movie from "./screens/Movie.svelte";
  import Player from "./screens/Player.svelte";
  import Search from "./screens/Search.svelte";
  import Show from "./screens/Show.svelte";
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

<!-- The player covers the film or show page it plays, which stays mounted under it. -->
<main inert={ui.sheetOpen || router.route.play != null}>
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
    {#key router.route.show}
      <Show id={router.route.show} />
    {/key}
  {/if}
</main>

{#if router.route.play}
  {#key playHref(router.route.play)}
    <Player target={router.route.play} />
  {/key}
{:else}
  <TabBar />
{/if}

<style>
  main {
    min-height: 100dvh;
    padding-bottom: var(--tabbar-space);
  }
</style>
