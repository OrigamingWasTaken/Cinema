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
  tab_home: "Home",
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

  tab_home: "Accueil",
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
