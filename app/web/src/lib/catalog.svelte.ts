// Everything /api/home knows: the rows on Home, what is on the server, free space.
// One poller for the whole app refreshes it every 3 s while a film is getting ready.
// /api/shows (the Shows tab and the Library's shows) has its own store below.
import {
  api, isActive, type Card, type EpisodeDownload, type HomeData, type LibraryItem, type ShowCard,
  type ShowsHome,
} from "./api";

const POLL_MS = 3000;

class Catalog {
  trending = $state<Card[]>([]);
  popular = $state<Card[]>([]);
  library = $state<LibraryItem[]>([]);
  episodes = $state<EpisodeDownload[]>([]);
  episodesFailed = $state<EpisodeDownload[]>([]);
  disk = $state<HomeData["disk"]>(null);
  /** null until the first answer; then whether the TMDB rows failed to load. */
  catalogError = $state<boolean | null>(null);
  /** The very first load failed (nothing to show at all). */
  failed = $state(false);
  loaded = $state(false);

  active = $derived(this.library.filter(isActive));
  done = $derived(this.library.filter((item) => item.state === "done"));
  broken = $derived(this.library.filter((item) => item.state === "failed"));

  #timer: ReturnType<typeof setTimeout> | null = null;
  #inFlight: Promise<void> | null = null;

  item(id: number): LibraryItem | null {
    return this.library.find((entry) => entry.tmdb_id === id) ?? null;
  }

  /** On the server, as far as we know (the live library wins over a card's flag). */
  state(card: Card): LibraryItem["state"] | null {
    if (!this.loaded) return card.in_library ? "done" : null;
    return this.item(card.id)?.state ?? null;
  }

  /** Fetch /api/home. Concurrent callers share one request. */
  refresh(): Promise<void> {
    this.#inFlight ??= this.#fetch().finally(() => {
      this.#inFlight = null;
      this.#schedule();
    });
    return this.#inFlight;
  }

  async #fetch() {
    try {
      const data = await api<HomeData>("/home");
      this.library = data.library;
      this.episodes = data.episodes;
      this.episodesFailed = data.episodes_failed;
      this.disk = data.disk;
      // A failed TMDB call keeps the rows we already have.
      if (!data.catalog_error) {
        this.trending = data.trending;
        this.popular = data.popular;
      }
      this.catalogError = !!data.catalog_error && this.trending.length === 0;
      this.loaded = true;
      this.failed = false;
    } catch {
      if (!this.loaded) this.failed = true;
    }
  }

  /** Put the server's answer for one film into the library (null = not on the server). */
  set(id: number, item: LibraryItem | null) {
    const rest = this.library.filter((entry) => entry.tmdb_id !== id);
    const current = this.item(id);
    if (!item) {
      if (current) this.library = rest;
    } else if (JSON.stringify(item) !== JSON.stringify(current)) {
      const at = this.library.findIndex((entry) => entry.tmdb_id === id);
      this.library = at < 0 ? [item, ...rest] : this.library.map((entry, i) => (i === at ? item : entry));
    }
    this.#schedule();
  }

  /** At most one pending tick; none while nothing is getting ready or the app is hidden. */
  #schedule() {
    if (this.#timer || this.#inFlight || document.hidden || !this.active.length) return;
    this.#timer = setTimeout(() => {
      this.#timer = null;
      this.refresh();
    }, POLL_MS);
  }

  start() {
    this.refresh();
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) {
        if (this.#timer) clearTimeout(this.#timer);
        this.#timer = null;
      } else {
        this.refresh();
      }
    });
  }
}

export const catalog = new Catalog();

// The Shows tab and the Library's shows. Fetched each time one of them is opened (the
// rows already shown stay up meanwhile); concurrent callers share one request.
class Shows {
  yours = $state<ShowCard[]>([]);
  library = $state<ShowCard[]>([]);
  trending = $state<ShowCard[]>([]);
  popular = $state<ShowCard[]>([]);
  /** Sonarr didn't answer: "Your shows" and the library are unknown. */
  sonarrError = $state(false);
  /** The TMDB rows failed and there are none to show. */
  catalogError = $state(false);
  /** The very first load failed (nothing to show at all). */
  failed = $state(false);
  loaded = $state(false);

  #inLibrary = $derived(new Set(this.library.map((card) => card.id)));
  #inFlight: Promise<void> | null = null;

  /** On the server, as far as we know (the live library wins over a card's flag). */
  state(card: ShowCard): "done" | null {
    const on = this.loaded && !this.sonarrError ? this.#inLibrary.has(card.id) : card.in_library;
    return on ? "done" : null;
  }

  refresh(): Promise<void> {
    this.#inFlight ??= this.#fetch().finally(() => {
      this.#inFlight = null;
    });
    return this.#inFlight;
  }

  async #fetch() {
    try {
      const data = await api<ShowsHome>("/shows");
      // Without Sonarr, "Your shows" and the library are hidden rather than shown stale.
      this.sonarrError = !!data.sonarr_error;
      this.yours = data.yours;
      this.library = data.library;
      // A failed TMDB call keeps the rows we already have.
      if (!data.catalog_error) {
        this.trending = data.trending;
        this.popular = data.popular;
      }
      this.catalogError = !!data.catalog_error && this.trending.length === 0;
      this.loaded = true;
      this.failed = false;
    } catch {
      if (!this.loaded) this.failed = true;
    }
  }
}

export const shows = new Shows();
