// The JSON API in app/cinema/api.py.

export type State = "preparing" | "ready" | "done" | "failed";

export interface Card {
  id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  backdrop_path: string | null;
  in_library: boolean;
}

export interface PersonRef {
  id: number;
  name: string;
  profile_path: string | null;
  known_for_department: string | null;
}

export interface StudioRef {
  id: number;
  name: string;
  logo_path: string | null;
}

export interface CollectionRef {
  id: number;
  name: string;
  poster_path: string | null;
  backdrop_path?: string | null;
}

export interface Genre {
  id: number;
  name: string;
}

export interface SearchData {
  films: Card[];
  shows: ShowCard[];
  people: PersonRef[];
  studios: StudioRef[];
  collections: CollectionRef[];
}

/** One page of a studio's or a genre's films. */
export interface FilmPage {
  films: Card[];
  page: number;
  total_pages: number;
}

export interface LibraryItem {
  tmdb_id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  state: State;
  percent: number;
  eta_s: number | null;
  may_buffer: boolean;
}

export interface HomeData {
  library: LibraryItem[];
  trending: Card[];
  popular: Card[];
  /** Episodes getting ready (newest first), and the ones that failed. */
  episodes: EpisodeDownload[];
  episodes_failed: EpisodeDownload[];
  disk: { free_bytes: number; total_bytes: number } | null;
  catalog_error?: boolean;
}

/** An episode being downloaded (or that failed), from /api/home. */
export interface EpisodeDownload {
  /** The show's TMDB id; null when Sonarr doesn't know it. */
  show_id: number | null;
  show_title: string;
  poster_path: string | null;
  season: number;
  episode: number;
  state: State;
  percent: number;
  eta_s: number | null;
  may_buffer: boolean;
}

/** A show card. `in_library` means it has episodes on the server. */
export interface ShowCard {
  id: number;
  title: string;
  year: number | null;
  poster_path: string | null;
  backdrop_path: string | null;
  in_library: boolean;
}

/** /api/shows. A failed Sonarr empties `yours` and `library`; a failed TMDB the rows. */
export interface ShowsHome {
  yours: ShowCard[];
  library: ShowCard[];
  trending: ShowCard[];
  popular: ShowCard[];
  sonarr_error?: boolean;
  catalog_error?: boolean;
}

export type SeasonState =
  | "none" | "upcoming" | "looking" | "not_found" | "downloading" | "done" | "partial";

export interface EpisodeView {
  number: number;
  title: string;
  /** Sonarr's ISO 8601 UTC air time, or null when unknown. */
  air_date: string | null;
  state: "on_tv" | "downloading" | "not_aired" | "missing";
}

export interface SeasonView {
  number: number;
  state: SeasonState;
  on_tv: number;
  total: number;
  aired: number;
  downloading: number;
  /** Sonarr monitors it: it was got (or followed) and not removed since. */
  monitored: boolean;
  /** null until the show is in the library (no episode list yet). */
  episodes: EpisodeView[] | null;
  /** TMDB's season name, when it is trustworthy and not just "Season N". */
  label: string | null;
  year: number | null;
  /** "YYYY-MM-DD" of the next episode, on the season it belongs to. */
  next_air_date: string | null;
}

export interface ShowInfo {
  id: number;
  title: string;
  year: number | null;
  /** The last year once the show has ended; null while airing. */
  end_year: number | null;
  overview: string;
  poster_path: string | null;
  backdrop_path: string | null;
  airing: boolean;
}

/** /api/shows/{id}, and the answer to every season and follow action. */
export interface ShowPage {
  show: ShowInfo;
  available: boolean;
  following: boolean;
  seasons: SeasonView[];
}

export interface MovieInfo {
  id: number;
  title: string;
  original_title: string;
  year: number | null;
  runtime: number | null;
  overview: string;
  poster_path: string | null;
  backdrop_path: string | null;
}

export interface MovieData {
  movie: MovieInfo;
  library: LibraryItem | null;
}

export interface ReleaseView {
  id: string;
  resolution: number;
  size_bytes: number;
  source: "bluray" | "web" | "other";
  sound: string;
  lang: "original" | "multi" | "french_subs";
  speed: "fast" | "ok" | "slow";
}

export interface ReleasesData {
  recommended: ReleaseView | null;
  groups: { resolution: number; standard: ReleaseView[]; atmos: ReleaseView[] }[];
}

export class ApiError extends Error {
  constructor(public status: number, public code: string | null) {
    super(code || `http ${status}`);
  }
}

interface Options {
  method?: string;
  body?: unknown;
  timeout?: number;
  signal?: AbortSignal;
}

/** Fetch /api{path}. Network failures and timeouts throw ApiError with status 0. */
export async function api<T>(path: string, { method = "GET", body, timeout = 20000, signal }: Options = {}): Promise<T> {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), timeout);
  const cancel = () => abort.abort();
  signal?.addEventListener("abort", cancel);
  try {
    const resp = await fetch(`/api${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      signal: abort.signal,
    });
    if (resp.status === 204) return null as T;
    const data = await resp.json().catch(() => null);
    if (!resp.ok) throw new ApiError(resp.status, data && data.error);
    return data as T;
  } catch (err) {
    if (signal?.aborted) throw new ApiError(-1, "aborted");
    throw err instanceof ApiError ? err : new ApiError(0, "network");
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", cancel);
  }
}

/** What Status shows: a film on the server, or an episode being downloaded. */
export type Progress = Pick<LibraryItem, "state" | "percent" | "eta_s" | "may_buffer">;

export const isActive = (item: Progress | null | undefined): boolean =>
  !!item && (item.state === "preparing" || item.state === "ready");

const IMAGE = "https://image.tmdb.org/t/p/";
export const posterUrl = (path: string) => `${IMAGE}w342${path}`;
export const profileUrl = (path: string, size: "w185" | "h632" = "w185") => `${IMAGE}${size}${path}`;
export const logoUrl = (path: string) => `${IMAGE}w185${path}`;
export const backdropUrl = (path: string, size: "w780" | "w1280" = "w1280") => `${IMAGE}${size}${path}`;
