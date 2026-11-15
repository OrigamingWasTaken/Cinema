<script lang="ts">
  import Poster from "./Poster.svelte";
  import StudioLogo from "./StudioLogo.svelte";

  // Search results: studios as logo tiles, collections as poster tiles.
  interface Tile { id: number; name: string; href: string; path: string | null }
  let { title, tiles, variant }: { title: string; tiles: Tile[]; variant: "logo" | "poster" } = $props();
</script>

<section class="row">
  <h2 class="row-title">{title}</h2>
  <ul class="scroller scroller--{variant}">
    {#each tiles as tile (tile.id)}
      <li>
        <a class="tile" href={tile.href} aria-label={tile.name}>
          <span class="art">
            {#if variant === "logo"}
              <StudioLogo path={tile.path} name={tile.name} />
            {:else}
              <Poster path={tile.path} title={tile.name} />
            {/if}
          </span>
          <!-- A studio without a logo already shows its name on the tile. -->
          {#if variant === "poster" || tile.path}
            <span class="name" aria-hidden="true">{tile.name}</span>
          {/if}
        </a>
      </li>
    {/each}
  </ul>
</section>

<style>
  .row { margin-top: 28px; }
  .row-title { margin: 0 var(--gutter) 12px; font: 600 20px/1.2 var(--serif); }
  .scroller {
    list-style: none;
    margin: 0;
    padding: 0 var(--gutter);
    display: grid;
    grid-auto-flow: column;
    gap: 10px;
    overflow-x: auto;
    overscroll-behavior-x: contain;
    scroll-snap-type: x mandatory;
    scroll-padding-inline: var(--gutter);
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
  }
  .scroller::-webkit-scrollbar { display: none; }
  .scroller--logo { grid-auto-columns: 40%; }
  .scroller--poster { grid-auto-columns: 31%; }
  @media (min-width: 600px) {
    .scroller--logo { grid-auto-columns: 180px; }
    .scroller--poster { grid-auto-columns: 150px; }
  }
  li { scroll-snap-align: start; min-width: 0; }
  .tile { display: flex; flex-direction: column; gap: 8px; min-width: 0; -webkit-touch-callout: none; }
  .art { display: block; transition: transform 0.18s ease; }
  .tile:active .art { transform: scale(0.965); }
  .name {
    padding: 0 2px;
    font-size: 13px;
    line-height: 1.25;
    font-weight: 550;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  @media (prefers-reduced-motion: reduce) { .art { transition: none; } }
</style>
