<script lang="ts">
  import type { Snippet } from "svelte";
  import Icon, { type IconName } from "./Icon.svelte";

  let { title, text, icon, children }:
    { title: string; text: string; icon?: IconName; children?: Snippet } = $props();
</script>

<div class="empty">
  {#if icon}<span class="glyph"><Icon name={icon} size={28} stroke={1.7} /></span>{/if}
  <p class="title">{title}</p>
  <p class="text">{text}</p>
  {#if children}<div class="action">{@render children()}</div>{/if}
</div>

<style>
  .empty {
    padding: max(48px, 12vh) calc(var(--gutter) + 12px) 24px;
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
  }
  p { margin: 0; }
  .glyph {
    width: 64px;
    height: 64px;
    margin-bottom: 8px;
    display: grid;
    place-items: center;
    border-radius: 50%;
    color: var(--usher);
    background: radial-gradient(circle at 50% 35%, rgb(243 194 107 / 0.12), rgb(255 255 255 / 0.04) 70%);
    box-shadow: inset 0 0 0 1px var(--line);
  }
  .title { font: 600 21px/1.25 var(--serif); }
  .text { color: var(--usher); max-width: 30ch; font-size: 15px; }
  .action { margin-top: 14px; }
</style>
