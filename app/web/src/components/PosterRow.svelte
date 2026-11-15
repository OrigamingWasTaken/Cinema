<script lang="ts">
  import type { Card, ShowCard } from "../lib/api";
  import { catalog, shows } from "../lib/catalog.svelte";
  import PosterCard from "./PosterCard.svelte";

  let { title, cards, kind = "movie" }:
    { title: string; cards: Card[] | ShowCard[]; kind?: "movie" | "show" } = $props();
</script>

<section class="row">
  <h2 class="row-title">{title}</h2>
  <ul class="scroller">
    {#each cards as card, i (card.id)}
      <li>
        <PosterCard id={card.id} title={card.title} year={card.year} path={card.poster_path}
                    state={kind === "show" ? shows.state(card) : catalog.state(card)} eager={i < 3}
                    href={kind === "show" ? `#/show/${card.id}` : undefined} />
      </li>
    {/each}
  </ul>
</section>

<style>
  .row { margin-top: 28px; }
  .row-title {
    margin: 0 var(--gutter) 12px;
    font: 600 20px/1.2 var(--serif);
    letter-spacing: 0.005em;
  }
  .scroller {
    list-style: none;
    margin: 0;
    padding: 0 var(--gutter);
    display: grid;
    grid-auto-flow: column;
    grid-auto-columns: 31%;
    gap: 10px;
    overflow-x: auto;
    overscroll-behavior-x: contain;
    scroll-snap-type: x mandatory;
    scroll-padding-inline: var(--gutter);
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
  }
  .scroller::-webkit-scrollbar { display: none; }
  li { scroll-snap-align: start; min-width: 0; }
  @media (min-width: 600px) { .scroller { grid-auto-columns: 150px; } }
</style>
