<script lang="ts">
  import type { PersonRef } from "../lib/api";
  import { t } from "../lib/i18n";
  import Avatar from "./Avatar.svelte";

  // Search results: actors and directors as a row of round photos.
  let { people }: { people: PersonRef[] } = $props();
</script>

<section class="row">
  <h2 class="row-title">{t("people")}</h2>
  <ul class="scroller">
    {#each people as person (person.id)}
      <li>
        <a class="person" href="#/person/{person.id}">
          <Avatar path={person.profile_path} name={person.name} />
          <span class="name">{person.name}</span>
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
    grid-auto-columns: 80px;
    gap: 10px;
    overflow-x: auto;
    overscroll-behavior-x: contain;
    scroll-snap-type: x proximity;
    scroll-padding-inline: var(--gutter);
    scrollbar-width: none;
    -webkit-overflow-scrolling: touch;
  }
  .scroller::-webkit-scrollbar { display: none; }
  li { scroll-snap-align: start; min-width: 0; }
  .person {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    padding-top: 2px;
    -webkit-touch-callout: none;
  }
  .person :global(.avatar) { transition: transform 0.18s ease; }
  .person:active :global(.avatar) { transform: scale(0.95); }
  .name {
    width: 100%;
    font-size: 13px;
    line-height: 1.25;
    font-weight: 550;
    text-align: center;
    overflow-wrap: break-word;
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  @media (prefers-reduced-motion: reduce) { .person :global(.avatar) { transition: none; } }
</style>
