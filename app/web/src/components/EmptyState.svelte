<script lang="ts">
  import type { Snippet } from "svelte";

  let { title, text, children }: { title: string; text: string; children?: Snippet } = $props();
</script>

<div class="empty">
  <p class="title">{title}</p>
  <p class="text">{text}</p>
  {#if children}<div class="action">{@render children()}</div>{/if}
</div>

<style>
  .empty {
    padding: 48px calc(var(--gutter) + 12px) 24px;
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
  }
  p { margin: 0; }
  .title { font: 600 21px/1.25 var(--serif); }
  .text { color: var(--usher); max-width: 30ch; font-size: 15px; }
  .action { margin-top: 14px; }
</style>
