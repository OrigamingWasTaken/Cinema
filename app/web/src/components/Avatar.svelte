<script lang="ts">
  import { profileUrl } from "../lib/api";

  // A person's round photo. Without one (or when it fails to load) the circle shows
  // their initials instead.
  let { path, name, size = 72, large = false }:
    { path: string | null; name: string; size?: number; large?: boolean } = $props();

  let loaded = $state(false);
  let broken = $state(false);

  const initials = $derived.by(() => {
    const words = name.trim().split(/\s+/).filter(Boolean);
    const letters = words.length > 1 ? [words[0], words[words.length - 1]] : words;
    return letters.map((word) => Array.from(word)[0] ?? "").join("").toUpperCase();
  });
</script>

<span class="avatar" class:loaded style:--size="{size}px" aria-hidden="true">
  {#if path && !broken}
    <img src={profileUrl(path, large ? "h632" : "w185")} alt="" loading="lazy" decoding="async"
         onload={() => (loaded = true)} onerror={() => (broken = true)} />
  {:else}
    <span class="initials">{initials}</span>
  {/if}
</span>

<style>
  .avatar {
    position: relative;
    width: var(--size);
    height: var(--size);
    flex: none;
    display: grid;
    place-items: center;
    border-radius: 50%;
    overflow: hidden;
    background:
      radial-gradient(120% 90% at 50% 0%, rgb(243 194 107 / 0.16), transparent 65%),
      linear-gradient(180deg, var(--velvet), var(--aisle));
    isolation: isolate;
  }
  .avatar::after {
    content: "";
    position: absolute;
    inset: 0;
    border-radius: inherit;
    box-shadow: inset 0 0 0 1px rgb(255 255 255 / 0.08);
    pointer-events: none;
  }
  img {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: 50% 20%;
    opacity: 0;
    transition: opacity 0.35s ease;
  }
  .loaded img { opacity: 1; }
  .initials {
    font: 600 calc(var(--size) * 0.36) / 1 var(--serif);
    letter-spacing: 0.02em;
    color: rgb(244 238 230 / 0.78);
  }
  @media (prefers-reduced-motion: reduce) { img { transition: none; } }
</style>
