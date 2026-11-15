// UI copy. French when the browser language starts with "fr", English otherwise.
// The first block of each language is the v1 copy table, kept word for word
// (none_found now names the "More versions" button).
const en = {
  recommended: "Recommended",
  more: "More",
  on_server: "On the server",
  trending: "Trending this week",
  popular: "Popular",
  search: "Search movies",
  finding: "Finding the best version…",
  preparing: "Getting ready…",
  ready: "Ready to watch on TV",
  may_buffer: "may pause to buffer",
  downloading: "Downloading {n}% · {m} min left",
  done: "On the TV",
  failed: "Couldn't download — try another version",
  none_found: "No good version yet — try More versions",
  remove: "Remove",
  remove_confirm: "Remove this movie from the TV?",
  standard: "Standard",
  atmos: "Dolby Atmos",
  bluray: "Blu-ray",
  web: "Streaming",
  lang_original: "Original",
  lang_multi: "Multi",
  lang_french_subs: "French subs",
  speed_fast: "Fast",
  speed_ok: "OK",
  speed_slow: "Slow",
  get_it: "Get this version",
  free_space: "{n} GB free",
  error: "Something went wrong — try again",
  size_gb: "{n} GB",
  runtime: "{h} h {m} min",
  no_results: "No movies found",
  back: "Back",
  close: "Close",

  // Added with the tabbed app.
  tab_films: "Films",
  tab_shows: "Shows",
  tab_search: "Search",
  tab_library: "Library",
  tab_downloads: "Downloads",
  get: "Get",
  details: "Details",
  more_versions: "More versions",
  popular_now: "Popular right now",
  no_results_hint: "Check the spelling, or try the original title.",
  clear: "Clear",
  cancel: "Cancel",
  library_empty_title: "Nothing on the TV yet",
  library_empty: "Films you get show up here once they're ready on the TV.",
  find_film: "Find a film",
  downloads_active: "Downloading",
  downloads_failed: "Couldn't download",
  downloads_empty_title: "Nothing downloading",
  downloads_empty: "Pick a film and tap Recommended. It shows up here while it gets ready.",
  try_another: "Try another version",
  choose_version: "Choose a version",
  no_versions: "No versions found yet. Try again later.",
  minutes: "{m} min",

  // Added with catalog search.
  films: "Films",
  people: "People",
  studios: "Studios",
  collections: "Collections",
  genres: "Genres",
  load_more: "Load more",
  films_with: "Films with {name}",
  browse_empty: "No films here yet",

  // Added with TV shows.
  your_shows: "Your shows",
  shows: "Shows",
  search_all: "Films, shows, people",
  nothing_found: "Nothing found",
  library_shows_empty_title: "No shows on the TV yet",
  library_shows_empty: "Shows you get show up here once their first episodes are on the TV.",
  find_show: "Find a show",

  // Added with the show page.
  get_season: "Get season",
  get_rest: "Get the rest",
  try_again: "Try again",
  follow: "Follow",
  following: "Following",
  unfollow: "Unfollow",
  season_n: "Season {n}",
  season_downloading: "Downloading {n} of {m} episodes",
  season_partial: "{n} of {m} on the TV",
  season_looking: "Looking for episodes…",
  season_not_found: "Couldn't find this season yet",
  season_upcoming: "Not out yet",
  season_next_episode: "New episode {date}",
  not_available: "Not available",
  remove_season_confirm: "Remove this season from the TV?",
  episode_failed: "Couldn't download — looking for another version",
  episode_n: "Episode {n}",
  episode_downloading: "Downloading",
  episode_not_aired: "Not out yet",

  // Added with browser playback.
  play: "Play",
  resume_from: "Resume from {t}",
  start_over: "Start over",
  play_busy: "Two people are already watching in a browser — try again in a bit",
  play_unsupported: "This one can't play in this browser — try Safari, or watch it on the TV",
};

type Key = keyof typeof en;

const fr: Record<Key, string> = {
  recommended: "Recommandé",
  more: "Plus",
  on_server: "Sur le serveur",
  trending: "Tendances de la semaine",
  popular: "Populaires",
  search: "Rechercher un film",
  finding: "Recherche de la meilleure version…",
  preparing: "Préparation…",
  ready: "Prêt à regarder sur la TV",
  may_buffer: "peut faire des pauses pour charger",
  downloading: "Téléchargement {n} % · {m} min restantes",
  done: "Sur la TV",
  failed: "Échec du téléchargement — essayez une autre version",
  none_found: "Pas encore de bonne version — essayez Plus de versions",
  remove: "Supprimer",
  remove_confirm: "Supprimer ce film de la TV ?",
  standard: "Standard",
  atmos: "Dolby Atmos",
  bluray: "Blu-ray",
  web: "Streaming",
  lang_original: "VO",
  lang_multi: "Multi",
  lang_french_subs: "VOSTFR",
  speed_fast: "Rapide",
  speed_ok: "OK",
  speed_slow: "Lent",
  get_it: "Prendre cette version",
  free_space: "{n} Go libres",
  error: "Un problème est survenu — réessayez",
  size_gb: "{n} Go",
  runtime: "{h} h {m} min",
  no_results: "Aucun film trouvé",
  back: "Retour",
  close: "Fermer",

  tab_films: "Films",
  tab_shows: "Séries",
  tab_search: "Rechercher",
  tab_library: "Bibliothèque",
  tab_downloads: "Téléchargements",
  get: "Obtenir",
  details: "Détails",
  more_versions: "Plus de versions",
  popular_now: "Populaires en ce moment",
  no_results_hint: "Vérifiez l'orthographe, ou essayez le titre original.",
  clear: "Effacer",
  cancel: "Annuler",
  library_empty_title: "Rien sur la TV pour l'instant",
  library_empty: "Les films que vous prenez apparaissent ici dès qu'ils sont prêts sur la TV.",
  find_film: "Trouver un film",
  downloads_active: "En cours",
  downloads_failed: "Échec du téléchargement",
  downloads_empty_title: "Aucun téléchargement",
  downloads_empty: "Choisissez un film et touchez Recommandé. Il apparaît ici pendant sa préparation.",
  try_another: "Essayer une autre version",
  choose_version: "Choisir une version",
  no_versions: "Aucune version trouvée pour l'instant. Réessayez plus tard.",
  minutes: "{m} min",

  films: "Films",
  people: "Personnes",
  studios: "Studios",
  collections: "Collections",
  genres: "Genres",
  load_more: "Plus de résultats",
  films_with: "Films avec {name}",
  browse_empty: "Aucun film ici pour l'instant",

  your_shows: "Vos séries",
  shows: "Séries",
  search_all: "Films, séries, personnes",
  nothing_found: "Aucun résultat",
  library_shows_empty_title: "Aucune série sur la TV pour l'instant",
  library_shows_empty: "Les séries que vous prenez apparaissent ici dès que leurs premiers épisodes sont sur la TV.",
  find_show: "Trouver une série",

  get_season: "Télécharger la saison",
  get_rest: "Télécharger la suite",
  try_again: "Réessayer",
  follow: "Suivre",
  following: "Suivie",
  unfollow: "Ne plus suivre",
  season_n: "Saison {n}",
  season_downloading: "{n} épisodes sur {m} en téléchargement",
  season_partial: "{n} sur {m} sur la TV",
  season_looking: "Recherche des épisodes…",
  season_not_found: "Saison introuvable pour l'instant",
  season_upcoming: "Pas encore sortie",
  season_next_episode: "Nouvel épisode {date}",
  not_available: "Non disponible",
  remove_season_confirm: "Retirer cette saison de la TV ?",
  episode_failed: "Échec du téléchargement — recherche d'une autre version",
  episode_n: "Épisode {n}",
  episode_downloading: "En téléchargement",
  episode_not_aired: "Pas encore sorti",

  play: "Lecture",
  resume_from: "Reprendre à {t}",
  start_over: "Reprendre du début",
  play_busy: "Deux personnes regardent déjà dans un navigateur — réessayez dans un moment",
  play_unsupported: "Impossible de lire ce titre dans ce navigateur — essayez Safari, ou regardez-le sur la TV",
};

export const STRINGS = { en, fr };
export type StringKey = Key;

export const lang: "en" | "fr" =
  (navigator.language || "en").toLowerCase().startsWith("fr") ? "fr" : "en";

const formats = new Map<number, Intl.NumberFormat>();

export function t(key: Key, vars: Record<string, string | number> = {}): string {
  const text = STRINGS[lang][key] ?? STRINGS.en[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, name: string) => (name in vars ? String(vars[name]) : ""));
}

export function formatNumber(n: number, digits = 0): string {
  let format = formats.get(digits);
  if (!format) {
    format = new Intl.NumberFormat(lang === "fr" ? "fr-FR" : "en-US", { maximumFractionDigits: digits });
    formats.set(digits, format);
  }
  return format.format(n);
}

/** "Downloading 43% · 12 min left"; the minutes part is dropped when the ETA is unknown. */
export function downloadingLine(percent: number, etaSeconds: number | null): string {
  if (etaSeconds == null) {
    return t("downloading", { n: percent }).split(" · ")[0];
  }
  return t("downloading", { n: percent, m: Math.max(1, Math.ceil(etaSeconds / 60)) });
}

export function gigabytes(bytes: number): string {
  const gb = bytes / 1e9;
  return t("size_gb", { n: formatNumber(gb, gb < 10 ? 1 : 0) });
}

export function runtimeText(minutes: number | null): string | null {
  if (!minutes) return null;
  if (minutes < 60) return t("minutes", { m: minutes });
  return t("runtime", { h: Math.floor(minutes / 60), m: String(minutes % 60).padStart(2, "0") });
}

/** "S02E03": Sonarr's season and episode numbers, two digits each. */
export function episodeCode(season: number, episode: number): string {
  const two = (n: number) => String(n).padStart(2, "0");
  return `S${two(season)}E${two(episode)}`;
}

/** "2016–2022", "2016–" while airing, "2024" for a single year. */
export function yearsText(year: number | null, endYear: number | null, airing: boolean): string | null {
  if (!year) return null;
  if (airing) return `${year}–`;
  return endYear && endYear !== year ? `${year}–${endYear}` : String(year);
}

// en-GB would write "Sept"; English takes en-US's month names in day-month-year order.
const dates = new Intl.DateTimeFormat(lang === "fr" ? "fr-FR" : "en-US", {
  day: "numeric", month: "short", year: "numeric",
});

/** An ISO 8601 time as a short local date ("12 Mar 2026", "12 mars 2026"); null if unreadable. */
export function shortDate(iso: string | null): string | null {
  if (!iso) return null;
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return null;
  if (lang === "fr") return dates.format(at);
  const parts = dates.formatToParts(at);
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${part("day")} ${part("month")} ${part("year")}`;
}

const days = new Intl.DateTimeFormat(lang === "fr" ? "fr-FR" : "en-US", {
  weekday: "short", day: "numeric", month: "short", timeZone: "UTC",
});

/** A "YYYY-MM-DD" air date as weekday, day and month ("Fri 20 Nov", "ven. 20 nov."); null if unreadable. */
export function shortDay(day: string | null): string | null {
  if (!day || !/^\d{4}-\d{2}-\d{2}$/.test(day)) return null;
  const at = new Date(`${day}T00:00:00Z`);
  if (Number.isNaN(at.getTime())) return null;
  if (lang === "fr") return days.format(at);
  const parts = days.formatToParts(at);
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${part("weekday")} ${part("day")} ${part("month")}`;
}
