<script lang="ts">
  import { slide } from "svelte/transition";
  import type { EpisodeView, SeasonView } from "../lib/api";
  import { shortDate, shortDay, t, type StringKey } from "../lib/i18n";
  import { ms } from "../lib/motion";
  import Icon from "./Icon.svelte";
  import Spinner from "./Spinner.svelte";

  // One season on the show page: its state, its one action (Get season, Get the rest,
  // Try again), Remove with a confirm, and its episodes on a tap. The page owns the
  // requests; `locked` disables every button while one is in flight.
  let { season, locked, pending, failed, onget, onremove }: {
    season: SeasonView;
    locked: boolean;
    /** This season's request in flight, if any. */
    pending: "get" | "remove" | null;
    /** This season's last request failed. */
    failed: boolean;
    onget: () => void;
    /** Resolves to whether the season was removed. */
    onremove: () => Promise<boolean>;
  } = $props();

  let open = $state(false);
  let confirming = $state(false);

  const ACTIONS: Partial<Record<SeasonView["state"], StringKey>> = {
    none: "get_season", upcoming: "get_season", not_found: "try_again", partial: "get_rest",
  };
  // A season already asked for that hasn't aired just waits: nothing to get yet.
  const action = $derived(season.state === "upcoming" && season.monitored ? null : (ACTIONS[season.state] ?? null));
  // Anything on the TV, or a season the server is getting: it can be removed.
  const removable = $derived(season.on_tv > 0 || season.monitored);
  const episodes = $derived(season.episodes?.length ? season.episodes : null);
  const listId = $derived(`season-${season.number}-episodes`);

  const sub = $derived([season.label, season.year].filter(Boolean).join(" · "));
  const nextDay = $derived(shortDay(season.next_air_date));

  const stateLine = $derived.by((): string | null => {
    switch (season.state) {
      case "done": return t("done");
      case "partial": return t("season_partial", { n: season.on_tv, m: season.total });
      case "downloading": return t("season_downloading", { n: season.downloading, m: season.total });
      case "looking": return t("season_looking");
      case "not_found": return t("season_not_found");
      case "upcoming": return t("season_upcoming");
      default: return null;
    }
  });

  const EPISODE_LABEL: Record<EpisodeView["state"], StringKey | null> = {
    on_tv: "done", downloading: "episode_downloading", not_aired: "episode_not_aired", missing: null,
  };

  $effect(() => {
    if (!removable) confirming = false;
  });

  async function remove() {
    if (await onremove()) confirming = false;
  }
</script>

<li class="season season--{season.state}">
  {#if episodes}
    <button class="head" type="button" aria-expanded={open} aria-controls={listId} onclick={() => (open = !open)}>
      <span class="name">{t("season_n", { n: season.number })}</span>
      <span class="chevron" class:open><Icon name="chevron" size={18} stroke={2.4} /></span>
    </button>
  {:else}
    <div class="head"><span class="name">{t("season_n", { n: season.number })}</span></div>
  {/if}

  {#if sub || nextDay}
    <p class="sub">
      {#if sub}<span>{sub}</span>{/if}
      {#if nextDay}<span class="next">{t("season_next_episode", { date: nextDay })}</span>{/if}
    </p>
  {/if}

  {#if stateLine || (removable && !confirming)}
    <div class="status">
      {#if stateLine}
        <p class="state">
          {#if season.state === "done" || season.state === "partial"}
            <Icon name="check" size={15} stroke={2.6} />
          {:else if season.state === "downloading" || season.state === "looking"}
            <Spinner size={13} />
          {:else if season.state === "not_found"}
            <Icon name="info" size={15} />
          {/if}
          <span>{stateLine}</span>
        </p>
      {/if}
      {#if removable && !confirming}
        <button class="btn btn--quiet remove" type="button" disabled={locked} onclick={() => (confirming = true)}>{t("remove")}</button>
      {/if}
    </div>
  {/if}

  {#if confirming}
    <div class="confirm">
      <p>{t("remove_season_confirm")}</p>
      <div class="confirm-buttons">
        <button class="btn btn--secondary" type="button" disabled={locked} onclick={() => (confirming = false)}>{t("cancel")}</button>
        <button class="btn btn--danger" type="button" disabled={locked} onclick={remove}>
          {#if pending === "remove"}<Spinner size={16} />{/if}{t("remove")}
        </button>
      </div>
    </div>
  {/if}

  {#if action}
    <div class="actions">
      <button class="btn btn--get" type="button" disabled={locked} onclick={onget}>
        {#if pending === "get"}<Spinner size={16} />{:else}<Icon name="get" size={18} stroke={2.4} />{/if}{t(action)}
      </button>
    </div>
  {/if}

  {#if failed}<p class="message" role="alert">{t("error")}</p>{/if}

  {#if open && episodes}
    <ol class="episodes" id={listId} transition:slide={{ duration: ms(220) }}>
      {#each episodes as ep (ep.number)}
        {@const date = shortDate(ep.air_date)}
        {@const label = EPISODE_LABEL[ep.state]}
        <li class="ep ep--{ep.state}">
          <span class="num">{ep.number}</span>
          <span class="ep-body">
            <span class="ep-title">{ep.title || t("episode_n", { n: ep.number })}</span>
            {#if date}<span class="ep-date">{date}</span>{/if}
          </span>
          <span class="ep-state">
            {#if ep.state === "on_tv"}<Icon name="check" size={16} stroke={2.6} />
            {:else if ep.state === "downloading"}<Spinner size={14} />{/if}
            {#if label}<span class="sr-only">{t(label)}</span>{/if}
          </span>
        </li>
      {/each}
    </ol>
  {/if}
</li>

<style>
  .season {
    border-radius: var(--r-card);
    background: var(--velvet);
    box-shadow: inset 0 0 0 1px var(--line);
    overflow: hidden;
    padding-bottom: 6px;
  }
  .head {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    min-height: 52px;
    padding: 10px 16px 6px;
    border: 0;
    background: none;
    text-align: left;
  }
  button.head { cursor: pointer; transition: background-color 0.15s ease; }
  button.head:active { background: var(--aisle); }
  .name { flex: 1; min-width: 0; font: 600 19px/1.2 var(--serif); }
  .chevron { flex: none; color: var(--usher); transform: rotate(90deg); transition: transform 0.2s ease; }
  .chevron.open { transform: rotate(-90deg); }

  .sub { display: flex; flex-direction: column; gap: 1px; margin: -2px 16px 6px; font-size: 14px; line-height: 1.3; color: var(--usher); }
  .sub .next { color: var(--tungsten); font-weight: 550; }

  .status { display: flex; align-items: center; gap: 10px; min-height: 44px; padding: 0 16px; margin-top: -6px; }
  .state {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
    gap: 7px;
    margin: 0;
    font-size: 14px;
    font-weight: 550;
    line-height: 1.3;
    color: var(--usher);
  }
  .state :global(svg), .state :global(.spinner) { flex: none; }
  .season--done .state { color: var(--tungsten); }
  .season--downloading .state, .season--looking .state { color: var(--screen); }
  .remove { flex: none; margin-left: auto; margin-right: -10px; padding: 0 10px; }

  .actions { padding: 4px 16px 10px; }
  .btn--get {
    min-height: 44px;
    padding: 0 16px 0 13px;
    gap: 8px;
    font-size: 15px;
    background: rgb(243 194 107 / 0.14);
    color: var(--tungsten);
  }
  .btn--get:active:not(:disabled) { background: rgb(243 194 107 / 0.22); }
  .btn--get:disabled, .remove:disabled { opacity: 0.5; }

  .confirm { margin: 6px 16px 10px; padding-top: 14px; border-top: 1px solid var(--line); }
  .confirm p { margin: 0 0 12px; font-weight: 550; }
  .confirm-buttons { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
  .confirm-buttons .btn { min-height: 48px; font-size: 16px; }
  .message { margin: 0 16px 10px; font-size: 14px; color: var(--failed); }

  .episodes { list-style: none; margin: 4px 0 -6px; padding: 4px 0 6px; border-top: 1px solid var(--line); }
  .ep { display: flex; align-items: center; gap: 12px; min-height: 52px; padding: 8px 16px; }
  .ep + .ep { box-shadow: inset 0 1px 0 rgb(255 255 255 / 0.04); }
  .num {
    flex: none;
    width: 22px;
    color: var(--usher);
    font-size: 14px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    text-align: right;
  }
  .ep-body { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  .ep-title { font-size: 15px; line-height: 1.3; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .ep-date { font-size: 13px; color: var(--usher); font-variant-numeric: tabular-nums; }
  .ep-state { flex: none; width: 20px; display: grid; place-items: center; color: var(--tungsten); }
  .ep--downloading .ep-state { color: var(--screen); }
  .ep--not_aired .ep-title, .ep--missing .ep-title { color: #cfc6d8; }
  .ep--not_aired .ep-title { opacity: 0.75; }

  @media (prefers-reduced-motion: reduce) {
    button.head, .chevron { transition: none; }
  }
</style>
