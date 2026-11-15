<script lang="ts">
  import ErrorBlock from "../components/ErrorBlock.svelte";
  import Icon from "../components/Icon.svelte";
  import PosterRow from "../components/PosterRow.svelte";
  import Spinner from "../components/Spinner.svelte";
  import Status from "../components/Status.svelte";
  import { backdropUrl, posterUrl } from "../lib/api";
  import { catalog } from "../lib/catalog.svelte";
  import { flow, getRecommended } from "../lib/flows.svelte";
  import { t } from "../lib/i18n";
  import { navigate, router } from "../lib/router.svelte";

  let { active }: { active: boolean } = $props();

  // The hero is the first trending film that has a backdrop.
  const hero = $derived(catalog.trending.find((card) => card.backdrop_path) ?? catalog.trending[0] ?? null);
  const heroItem = $derived(hero ? catalog.item(hero.id) : null);
  const heroFlow = $derived(hero ? flow(hero.id) : null);
  const heroArt = $derived(
    hero?.backdrop_path ? backdropUrl(hero.backdrop_path) : hero?.poster_path ? posterUrl(hero.poster_path) : null);
  let artLoaded = $state(false);

  async function get() {
    if (!hero) return;
    const id = hero.id;
    const outcome = await getRecommended(id);
    // Only open the film if the family is still looking at Home.
    const onHome = router.route.tab === "home" && router.route.movie == null;
    if (outcome === "none" && onHome) {
      // Nothing good to recommend: open the film with its version list.
      navigate(`#/movie/${id}`);
      navigate(`#/movie/${id}/more`);
    }
  }
</script>

<div class="home" inert={!active}>
  <header class="brand" aria-hidden="true"><span class="mark"></span>Cinema</header>

  {#if catalog.failed}
    <div class="pad-top"><ErrorBlock retry={() => catalog.refresh()} /></div>
  {:else if !catalog.loaded}
    <div class="hero hero--skeleton" aria-hidden="true">
      <div class="hero-copy">
        <div class="bone bone--title"></div>
        <div class="bone bone--btn"></div>
      </div>
    </div>
    {#each [0, 1] as row (row)}
      <div class="bone-row" aria-hidden="true">
        <div class="bone bone--heading"></div>
        <div class="bone-posters">{#each [0, 1, 2, 3] as p (p)}<div class="bone bone--poster"></div>{/each}</div>
      </div>
    {/each}
  {:else if catalog.catalogError}
    <div class="pad-top"><ErrorBlock retry={() => catalog.refresh()} /></div>
  {:else}
    {#if hero}
      <section class="hero" aria-label={hero.title}>
        <a class="hero-art" href="#/movie/{hero.id}" tabindex="-1" aria-hidden="true">
          {#if heroArt}
            <img src={heroArt} alt="" class:loaded={artLoaded} onload={() => (artLoaded = true)}
                 fetchpriority="high" decoding="async" />
          {/if}
        </a>
        <div class="hero-copy">
          <h1 class="hero-title"><a href="#/movie/{hero.id}">{hero.title}</a></h1>
          {#if hero.year}<p class="hero-year">{hero.year}</p>{/if}

          {#if heroItem && heroItem.state !== "failed"}
            <div class="hero-status">
              <Status item={heroItem} compact />
            </div>
            <div class="hero-actions">
              <a class="btn btn--secondary btn--grow" href="#/movie/{hero.id}">
                <Icon name="info" size={20} />{t("details")}
              </a>
            </div>
          {:else}
            <div class="hero-actions" aria-live="polite">
              <button class="btn btn--primary btn--grow" type="button" disabled={heroFlow?.finding} onclick={get}>
                {#if heroFlow?.finding}<Spinner />{t("finding")}{:else}<Icon name="get" size={20} stroke={2.4} />{t("get")}{/if}
              </button>
              <a class="btn btn--secondary btn--icon" href="#/movie/{hero.id}" aria-label={t("details")}>
                <Icon name="info" size={22} />
              </a>
            </div>
            {#if heroFlow?.message === "error"}
              <p class="hero-message">{t("error")}</p>
            {:else if heroItem?.state === "failed"}
              <p class="hero-message">{t("failed")}</p>
            {/if}
          {/if}
        </div>
      </section>
    {/if}

    {#if catalog.trending.length}
      <PosterRow title={t("trending")} cards={catalog.trending} />
    {/if}
    {#if catalog.popular.length}
      <PosterRow title={t("popular")} cards={catalog.popular} />
    {/if}
  {/if}
</div>

<style>
  .home { position: relative; padding-bottom: 12px; }
  .pad-top { padding-top: calc(env(safe-area-inset-top) + 56px); }

  .brand {
    position: absolute;
    z-index: 3;
    top: calc(env(safe-area-inset-top) + 14px);
    left: var(--gutter);
    display: flex;
    align-items: center;
    gap: 8px;
    font: 650 19px/1 var(--serif);
    letter-spacing: 0.01em;
    text-shadow: 0 1px 12px rgb(0 0 0 / 0.6);
  }
  .mark {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    background: var(--tungsten);
    box-shadow: 0 0 14px 2px rgb(243 194 107 / 0.55);
  }

  /* ---- hero: the backdrop runs under the status bar and fades into the curtain */
  .hero {
    position: relative;
    min-height: min(72vh, 600px);
    display: flex;
    flex-direction: column;
    justify-content: flex-end;
    isolation: isolate;
  }
  .hero-art {
    position: absolute;
    inset: 0;
    z-index: -1;
    overflow: hidden;
    background: var(--skeleton);
  }
  .hero-art img {
    width: 100%;
    height: 100%;
    object-fit: cover;
    object-position: 50% 30%;
    opacity: 0;
    transform: scale(1.04);
    transition: opacity 0.7s ease, transform 1.4s cubic-bezier(0.2, 0.7, 0.2, 1);
  }
  .hero-art img.loaded { opacity: 1; transform: none; }
  .hero-art::after {
    content: "";
    position: absolute;
    inset: 0;
    background:
      linear-gradient(180deg, rgb(21 17 27 / 0.55) 0%, transparent 22%),
      linear-gradient(0deg, var(--curtain) 2%, rgb(21 17 27 / 0.86) 26%, rgb(21 17 27 / 0.25) 58%, transparent 75%);
  }
  .hero-copy {
    padding: 0 var(--gutter) 4px;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .hero-title {
    margin: 0;
    font: 650 clamp(30px, 9vw, 44px)/1.05 var(--serif);
    letter-spacing: -0.01em;
    text-wrap: balance;
    text-shadow: 0 2px 24px rgb(0 0 0 / 0.5);
  }
  .hero-year { margin: 0; color: var(--usher); font-size: 15px; }
  .hero-actions { display: flex; gap: 10px; margin-top: 12px; }
  .btn--grow { flex: 1; }
  .btn--icon { width: 52px; padding: 0; flex: none; }
  .hero-status {
    margin-top: 10px;
    padding: 12px 14px;
    border-radius: var(--r-card);
    background: rgb(32 26 40 / 0.7);
    -webkit-backdrop-filter: blur(14px);
    backdrop-filter: blur(14px);
    box-shadow: inset 0 0 0 1px var(--line);
  }
  .hero-message { margin: 4px 0 0; font-size: 14px; color: var(--failed); }

  /* ---- skeleton */
  .bone { background: var(--skeleton); border-radius: 8px; }
  .hero--skeleton { background: linear-gradient(180deg, #1d1724, var(--curtain)); }
  .bone--title { width: 62%; height: 36px; }
  .bone--btn { width: 100%; height: 52px; border-radius: var(--r-button); margin-top: 14px; }
  .bone-row { margin-top: 28px; }
  .bone--heading { width: 46%; height: 20px; margin: 0 var(--gutter) 12px; }
  .bone-posters { display: grid; grid-template-columns: repeat(4, 31%); gap: 10px; padding: 0 var(--gutter); overflow: hidden; }
  .bone--poster { aspect-ratio: 2 / 3; border-radius: var(--r-poster); }

  @media (prefers-reduced-motion: reduce) {
    .hero-art img { transition: none; transform: none; }
  }
</style>
