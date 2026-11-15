<script lang="ts">
  import type { State } from "../lib/api";
  import PosterCard from "./PosterCard.svelte";

  interface Item { id: number; title: string; year: number | null; path: string | null; state: State | null; href?: string }
  let { items }: { items: Item[] } = $props();
</script>

<ul class="grid">
  {#each items as item, i (item.id)}
    <li><PosterCard {...item} caption eager={i < 6} /></li>
  {/each}
</ul>

<style>
  .grid {
    list-style: none;
    margin: 0;
    padding: 0 var(--gutter);
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 18px 10px;
  }
  @media (min-width: 600px) { .grid { grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); } }
</style>
