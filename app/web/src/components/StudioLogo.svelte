<script lang="ts">
  import { logoUrl } from "../lib/api";

  // A studio's logo on a light plate (logos are drawn for white backgrounds). Without
  // a logo, or when it fails to load, the plate shows the studio's name instead.
  let { path, name }: { path: string | null; name: string } = $props();

  let broken = $state(false);
</script>

{#if path && !broken}
  <span class="plate" aria-hidden="true">
    <img src={logoUrl(path)} alt="" loading="lazy" decoding="async" onerror={() => (broken = true)} />
  </span>
{:else}
  <span class="plate plate--name" aria-hidden="true"><span>{name}</span></span>
{/if}

<style>
  .plate {
    display: grid;
    place-items: center;
    width: 100%;
    aspect-ratio: 7 / 4;
    padding: 14% 14%;
    border-radius: var(--r-poster);
    background: #f4eee6;
    box-shadow: inset 0 0 0 1px rgb(255 255 255 / 0.06);
    overflow: hidden;
  }
  img { width: 100%; height: 100%; object-fit: contain; }
  .plate--name {
    padding: 10px 12px;
    background:
      radial-gradient(120% 90% at 50% 0%, rgb(243 194 107 / 0.14), transparent 65%),
      linear-gradient(180deg, var(--velvet), var(--aisle));
  }
  .plate--name span {
    font: 600 16px/1.15 var(--serif);
    color: var(--screen);
    text-align: center;
    overflow-wrap: break-word;
    display: -webkit-box;
    -webkit-line-clamp: 3;
    line-clamp: 3;
    -webkit-box-orient: vertical;
    overflow: hidden;
  }
</style>
