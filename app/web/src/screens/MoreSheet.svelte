<script lang="ts">
  import { untrack } from "svelte";
  import ErrorBlock from "../components/ErrorBlock.svelte";
  import Icon from "../components/Icon.svelte";
  import Sheet from "../components/Sheet.svelte";
  import Spinner from "../components/Spinner.svelte";
  import type { ReleasesData, ReleaseView } from "../lib/api";
  import { cachedReleases, clearMessage, releases, startDownload } from "../lib/flows.svelte";
  import { gigabytes, t, type StringKey } from "../lib/i18n";

  // Every version the server found, by resolution, Standard before Dolby Atmos.
  let { id, title, onclose }: { id: number; title: string; onclose: () => void } = $props();

  // The sheet is created for one film; `id` never changes while it is open.
  let data = $state<ReleasesData | null>(untrack(() => cachedReleases(id)));
  let loadFailed = $state(false);
  let pick = $state<string | null>(null);
  let busy = $state(false);
  let pickFailed = $state(false);

  async function load(fresh = false) {
    loadFailed = false;
    try {
      data = await releases(id, { fresh });
    } catch {
      loadFailed = true;
    }
  }
  untrack(() => {
    if (!data) load();
  });

  function facts(rel: ReleaseView): string[] {
    return [
      rel.source !== "other" ? t(rel.source) : null,
      rel.sound === "atmos" ? t("atmos") : rel.sound,
      t(`lang_${rel.lang}` as StringKey),
    ].filter((f): f is string => !!f);
  }

  function choose(rel: ReleaseView) {
    if (busy) return;
    pick = pick === rel.id ? null : rel.id;
    pickFailed = false;
  }

  async function confirm() {
    if (!pick || busy) return;
    busy = true;
    pickFailed = false;
    try {
      const outcome = await startDownload(id, pick);
      busy = false;
      if (outcome === "expired") {
        // The server forgot these versions; list them again.
        pick = null;
        data = null;
        await load(true);
        return;
      }
      clearMessage(id);
      onclose();
    } catch {
      busy = false;
      pickFailed = true;
    }
  }
</script>

{#snippet releaseRow(rel: ReleaseView)}
  {@const picked = pick === rel.id}
  <li>
    <button class="release" class:picked type="button" role="radio" aria-checked={picked} onclick={() => choose(rel)}>
      <span class="size">{gigabytes(rel.size_bytes)}</span>
      <span class="facts">
        {#each facts(rel) as fact, i (i)}<span class="fact">{fact}</span>{/each}
      </span>
      <span class="speed speed--{rel.speed}"><span class="bars" aria-hidden="true"><i></i><i></i><i></i></span>{t(`speed_${rel.speed}` as StringKey)}</span>
      <span class="tick" aria-hidden="true">{#if picked}<Icon name="check" size={14} stroke={3} />{/if}</span>
    </button>
  </li>
{/snippet}

{#snippet footer()}
  {#if pickFailed}<p class="message">{t("error")}</p>{/if}
  <button class="btn btn--primary btn--block" type="button" disabled={!pick || busy} onclick={confirm}>
    {#if busy}<Spinner />{/if}{t("get_it")}
  </button>
{/snippet}

<Sheet {title} subtitle={t("choose_version")} {onclose} footer={data && data.groups.length ? footer : undefined}>
  {#if data}
    {#if data.groups.length}
      <div role="radiogroup" aria-label={t("choose_version")}>
        {#each data.groups as group (group.resolution)}
          <section class="group">
            <h3 class="res">{group.resolution}p</h3>
            {#if group.standard.length}
              <h4 class="kind">{t("standard")}</h4>
              <ul class="releases">{#each group.standard as rel (rel.id)}{@render releaseRow(rel)}{/each}</ul>
            {/if}
            {#if group.atmos.length}
              <h4 class="kind">{t("atmos")}</h4>
              <ul class="releases">{#each group.atmos as rel (rel.id)}{@render releaseRow(rel)}{/each}</ul>
            {/if}
          </section>
        {/each}
      </div>
    {:else}
      <p class="none">{t("none_found")}</p>
    {/if}
  {:else if loadFailed}
    <div class="flush"><ErrorBlock retry={() => load()} /></div>
  {:else}
    <p class="finding"><Spinner />{t("finding")}</p>
  {/if}
</Sheet>

<style>
  .group + .group { margin-top: 22px; }
  .res {
    margin: 4px 0 8px;
    font: 650 17px/1.2 var(--sans);
    font-variant-numeric: tabular-nums;
  }
  .kind { margin: 12px 0 8px; font-size: 13px; font-weight: 600; color: var(--usher); }
  .releases { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
  .release {
    width: 100%;
    min-height: 56px;
    display: grid;
    grid-template-columns: 64px 1fr auto 22px;
    align-items: center;
    gap: 10px;
    padding: 10px 12px;
    border: 0;
    border-radius: 12px;
    background: var(--aisle);
    box-shadow: inset 0 0 0 1px var(--line);
    text-align: left;
    cursor: pointer;
    transition: box-shadow 0.15s ease, background-color 0.15s ease;
  }
  .release:active { background: #33293e; }
  .release.picked { background: rgb(243 194 107 / 0.1); box-shadow: inset 0 0 0 1.5px var(--tungsten); }
  .size { font-weight: 650; font-size: 16px; font-variant-numeric: tabular-nums; white-space: nowrap; }
  .facts { display: flex; flex-wrap: wrap; gap: 4px 6px; min-width: 0; }
  .fact {
    font-size: 12.5px;
    line-height: 1.2;
    padding: 3px 7px;
    border-radius: 6px;
    background: rgb(255 255 255 / 0.07);
    color: #d8cfe0;
    white-space: nowrap;
  }
  .speed { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600; white-space: nowrap; }
  .bars { display: inline-flex; align-items: flex-end; gap: 2px; height: 12px; }
  .bars i { width: 3px; border-radius: 1px; background: rgb(255 255 255 / 0.18); }
  .bars i:nth-child(1) { height: 5px; }
  .bars i:nth-child(2) { height: 8px; }
  .bars i:nth-child(3) { height: 12px; }
  .speed--fast { color: var(--ready); }
  .speed--fast .bars i { background: currentColor; }
  .speed--ok { color: var(--tungsten); }
  .speed--ok .bars i:nth-child(-n + 2) { background: currentColor; }
  .speed--slow { color: var(--failed); }
  .speed--slow .bars i:nth-child(1) { background: currentColor; }
  .tick {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    box-shadow: inset 0 0 0 1.5px rgb(255 255 255 / 0.2);
  }
  .picked .tick { background: var(--tungsten); color: var(--tungsten-ink); box-shadow: none; }
  .finding, .none {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    min-height: 140px;
    margin: 0;
    color: var(--usher);
    text-align: center;
  }
  .flush { margin: 0 calc(-1 * var(--gutter)); }
  .message { margin: 0 0 10px; color: var(--failed); font-size: 14px; text-align: center; }
  @media (prefers-reduced-motion: reduce) { .release { transition: none; } }
</style>
