<script lang="ts">
  import type { Snippet } from "svelte";
  import { cubicOut } from "svelte/easing";
  import { fade, fly } from "svelte/transition";
  import { t } from "../lib/i18n";
  import { ms } from "../lib/motion";
  import { portal, ui } from "../lib/ui.svelte";
  import Icon from "./Icon.svelte";

  // A bottom sheet. The page behind it stops scrolling; the scrim, the close button,
  // Escape and the phone's back button (the caller's route) all dismiss it.
  let { title, subtitle = "", onclose, children, footer }:
    { title: string; subtitle?: string; onclose: () => void; children: Snippet; footer?: Snippet } = $props();

  let panel: HTMLElement | undefined = $state();

  $effect(() => {
    document.body.classList.add("locked");
    ui.sheetOpen = true;
    const opener = document.activeElement as HTMLElement | null;
    panel?.focus({ preventScroll: true });
    return () => {
      document.body.classList.remove("locked");
      ui.sheetOpen = false;
      // Wait a frame so the page is no longer inert before focus goes back to it.
      requestAnimationFrame(() => opener?.focus?.({ preventScroll: true }));
    };
  });

  function keydown(event: KeyboardEvent) {
    if (event.key === "Escape") onclose();
  }
</script>

<svelte:window onkeydown={keydown} />

<!-- Rendered under <body> so the page behind it can be made inert (focus stays in the sheet). -->
<div class="layer" use:portal>
  <button class="scrim" type="button" tabindex="-1" aria-label={t("close")} onclick={onclose}
          transition:fade={{ duration: ms(220) }}></button>
  <div class="sheet" role="dialog" aria-modal="true" aria-label={title} tabindex="-1" bind:this={panel}
       transition:fly={{ y: 480, duration: ms(320), easing: cubicOut, opacity: 1 }}>
    <div class="grabber" aria-hidden="true"></div>
    <header class="head">
      <div class="head-text">
        <h2 class="title">{title}</h2>
        {#if subtitle}<p class="subtitle">{subtitle}</p>{/if}
      </div>
      <button class="close" type="button" aria-label={t("close")} onclick={onclose}>
        <Icon name="close" size={18} stroke={2.4} />
      </button>
    </header>
    <div class="body">{@render children()}</div>
    {#if footer}<div class="foot">{@render footer()}</div>{/if}
  </div>
</div>

<style>
  .layer { position: fixed; inset: 0; z-index: 50; display: flex; flex-direction: column; justify-content: flex-end; }
  .scrim {
    position: absolute;
    inset: 0;
    border: 0;
    padding: 0;
    background: rgb(8 6 11 / 0.66);
    -webkit-backdrop-filter: blur(3px);
    backdrop-filter: blur(3px);
    cursor: default;
  }
  .sheet {
    position: relative;
    width: min(100%, 640px);
    margin: 0 auto;
    max-height: calc(100dvh - env(safe-area-inset-top) - 36px);
    display: flex;
    flex-direction: column;
    background: var(--velvet);
    border-radius: 22px 22px 0 0;
    box-shadow: 0 -12px 40px rgb(0 0 0 / 0.5), inset 0 1px 0 rgb(255 255 255 / 0.07);
    outline: none;
  }
  .grabber { width: 38px; height: 5px; border-radius: 3px; background: rgb(255 255 255 / 0.22); margin: 8px auto 0; flex: none; }
  .head { display: flex; align-items: flex-start; gap: 12px; padding: 12px 12px 12px var(--gutter); flex: none; }
  .head-text { flex: 1; min-width: 0; }
  .title { margin: 0; font: 650 22px/1.2 var(--serif); }
  .subtitle { margin: 2px 0 0; color: var(--usher); font-size: 14px; }
  .close {
    width: 44px;
    height: 44px;
    display: grid;
    place-items: center;
    flex: none;
    border: 0;
    border-radius: 50%;
    background: rgb(255 255 255 / 0.08);
    color: var(--screen);
    cursor: pointer;
    margin-top: -4px;
  }
  .close:active { background: rgb(255 255 255 / 0.14); }
  .body {
    flex: 1;
    min-height: 120px;
    overflow-y: auto;
    overscroll-behavior: contain;
    -webkit-overflow-scrolling: touch;
    padding: 0 var(--gutter) 16px;
  }
  .foot {
    flex: none;
    padding: 12px var(--gutter) calc(env(safe-area-inset-bottom) + 14px);
    border-top: 1px solid var(--line);
    background: var(--velvet);
  }
  .sheet:not(:has(.foot)) .body { padding-bottom: calc(env(safe-area-inset-bottom) + 20px); }
</style>
