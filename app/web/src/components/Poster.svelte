<script lang="ts">
  import { posterUrl } from "../lib/api";

  // A 2:3 poster. The box is sized before the image arrives (no layout shift); a
  // missing or broken poster becomes a title tile.
  let { path, title, year = null, eager = false }:
    { path: string | null; title: string; year?: number | null; eager?: boolean } = $props();

  let loaded = $state(false);
  let broken = $state(false);
</script>

<div class="poster" class:loaded>
  {#if path && !broken}
    <img
      src={posterUrl(path)}
      alt=""
      loading={eager ? "eager" : "lazy"}
      decoding="async"
      onload={() => (loaded = true)}
      onerror={() => (broken = true)}
    />
  {:else}
    <div class="tile" aria-hidden="true">
      <span class="tile-initial">{title.trim().charAt(0)}</span>
      <span class="tile-title">{title}</span>
      {#if year}<span class="tile-year">{year}</span>{/if}
    </div>
  {/if}
</div>

<style>
  .poster {
    position: relative;
    aspect-ratio: 2 / 3;
    width: 100%;
    border-radius: var(--r-poster);
    overflow: hidden;
    background: var(--skeleton);
    isolation: isolate;
    container-type: inline-size;
  }
  /* A slow sweep of light while the image loads, like a projector warming up. */
  .poster:not(.loaded)::before {
    content: "";
    position: absolute;
    inset: 0;
    background: linear-gradient(100deg, transparent 30%, rgb(255 255 255 / 0.05) 50%, transparent 70%);
    background-size: 220% 100%;
    animation: sweep 1.6s ease-in-out infinite;
  }
  .poster::after {
    content: "";
    position: absolute;
    inset: 0;
    border-radius: inherit;
    box-shadow: inset 0 0 0 1px rgb(255 255 255 / 0.06);
    pointer-events: none;
  }
  img {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    opacity: 0;
    transition: opacity 0.35s ease;
  }
  .loaded img { opacity: 1; }
  .tile {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    gap: 4px;
    padding: 12% 10%;
    background:
      radial-gradient(120% 70% at 50% 0%, rgb(243 194 107 / 0.16), transparent 60%),
      linear-gradient(180deg, var(--velvet), var(--aisle));
  }
  .tile-title {
    font: 600 clamp(10px, 8cqw + 5px, 19px) / 1.15 var(--serif);
    color: var(--screen);
    overflow-wrap: break-word;
    display: -webkit-box;
    -webkit-line-clamp: 4;
    line-clamp: 4;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
  .tile-year { font-size: 12px; color: var(--usher); }
  .tile-initial { display: none; }
  /* A thumbnail is too small for a title: show its initial instead. */
  @container (max-width: 90px) {
    .tile { justify-content: center; align-items: center; }
    .tile-title, .tile-year { display: none; }
    .tile-initial { display: block; font: 600 30cqw/1 var(--serif); color: rgb(244 238 230 / 0.7); }
  }
  .poster:has(.tile)::before { display: none; }
  @keyframes sweep { from { background-position: 120% 0; } to { background-position: -120% 0; } }
  @media (prefers-reduced-motion: reduce) {
    .poster:not(.loaded)::before { animation: none; }
    img { transition: none; }
  }
</style>
