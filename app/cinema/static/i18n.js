// UI copy. French when the browser language starts with "fr", English otherwise.
const STRINGS = {
  en: {
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
    none_found: "No good version yet — try More",
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
    // Not in the copy table: units, empty search, and labels for icon-only buttons.
    size_gb: "{n} GB",
    runtime: "{h} h {m} min",
    no_results: "No movies found",
    back: "Back",
    close: "Close",
  },
  fr: {
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
    none_found: "Pas encore de bonne version — essayez Plus",
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
  },
};

export const lang = (navigator.language || "en").toLowerCase().startsWith("fr") ? "fr" : "en";

const numberFormat = (digits) =>
  new Intl.NumberFormat(lang === "fr" ? "fr-FR" : "en-US", { maximumFractionDigits: digits });

export function t(key, vars = {}) {
  const text = STRINGS[lang][key] ?? STRINGS.en[key] ?? key;
  return text.replace(/\{(\w+)\}/g, (_, name) => (name in vars ? String(vars[name]) : ""));
}

export function formatNumber(n, digits = 0) {
  return numberFormat(digits).format(n);
}

/** "Downloading 43% · 12 min left"; the minutes part is dropped when the ETA is unknown. */
export function downloadingLine(percent, etaSeconds) {
  if (etaSeconds == null) {
    return t("downloading", { n: percent }).split(" · ")[0];
  }
  return t("downloading", { n: percent, m: Math.max(1, Math.ceil(etaSeconds / 60)) });
}
