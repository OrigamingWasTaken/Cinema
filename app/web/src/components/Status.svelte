<script lang="ts">
  import { isActive, type Progress } from "../lib/api";
  import { downloadingLine, t } from "../lib/i18n";
  import Icon from "./Icon.svelte";
  import Spinner from "./Spinner.svelte";

  // The family-facing status of a film (or an episode) on the server: one line of
  // copy, then the progress while it is still coming in.
  let { item, compact = false }: { item: Progress; compact?: boolean } = $props();
</script>

<div class="status status--{item.state}" class:compact>
  <p class="headline">
    {#if item.state === "preparing"}
      <Spinner size={14} /><span>{t("preparing")}</span>
    {:else if item.state === "ready"}
      <span class="dot" aria-hidden="true"></span><span>{t("ready")}</span>
    {:else if item.state === "done"}
      <Icon name="check" size={16} stroke={2.6} /><span>{t("done")}</span>
    {:else}
      <Icon name="alert" size={16} /><span>{t("failed")}</span>
    {/if}
  </p>
  {#if item.state === "ready" && item.may_buffer}
    <p class="note">{t("may_buffer")}</p>
  {/if}
  {#if isActive(item)}
    <div class="bar" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow={item.percent}
         aria-label={downloadingLine(item.percent, item.eta_s)}>
      <div class="fill" style:transform="scaleX({Math.max(item.percent, 1.5) / 100})"></div>
    </div>
    <p class="sub">{downloadingLine(item.percent, item.eta_s)}</p>
  {/if}
</div>

<style>
  .status { display: flex; flex-direction: column; gap: 6px; min-width: 0; }
  p { margin: 0; }
  .headline {
    display: flex;
    align-items: center;
    gap: 8px;
    font-weight: 600;
    font-size: 16px;
    line-height: 1.3;
  }
  .headline :global(svg), .headline :global(.spinner) { flex: none; }
  .compact .headline { font-size: 14px; gap: 6px; }
  .status--preparing .headline { color: var(--screen); }
  .status--ready .headline { color: var(--ready); }
  .status--done .headline { color: var(--tungsten); }
  .status--failed .headline { color: var(--failed); align-items: flex-start; }
  .status--failed .headline :global(svg) { margin-top: 1px; }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: currentColor;
    box-shadow: 0 0 0 4px rgb(159 216 176 / 0.18);
    margin: 0 3px;
  }
  .note { font-size: 13px; color: var(--usher); margin-top: -2px; }
  .bar {
    height: 4px;
    border-radius: 2px;
    background: rgb(255 255 255 / 0.1);
    overflow: hidden;
    margin-top: 4px;
  }
  .fill {
    height: 100%;
    background: linear-gradient(90deg, #d9a04a, var(--tungsten));
    transform-origin: left;
    transition: transform 0.6s ease;
    border-radius: inherit;
  }
  .sub { font-size: 13px; color: var(--usher); font-variant-numeric: tabular-nums; }
  @media (prefers-reduced-motion: reduce) { .fill { transition: none; } }
</style>
