import { downloadingLine, formatNumber, lang, t } from "./i18n.js";

const POSTER = "https://image.tmdb.org/t/p/w342";
const BACKDROP = "https://image.tmdb.org/t/p/w1280";
const POLL_MS = 3000;
const SEARCH_DELAY_MS = 350;
const RELEASES_FRESH_MS = 5 * 60 * 1000; // the server keeps release ids for 10 min

const view = document.getElementById("view");
const topbar = document.getElementById("topbar");
const searchForm = document.getElementById("search-form");
const searchInput = document.getElementById("search-input");
const sheetLayer = document.getElementById("sheet-layer");
const sheet = document.getElementById("sheet");

document.documentElement.lang = lang;
searchInput.placeholder = t("search");
searchInput.setAttribute("aria-label", t("search"));

// ---------------------------------------------------------------- helpers

/** Build an element. Text is always set as text, never parsed as HTML. */
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value == null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child != null && child !== false) el.append(child);
  }
  return el;
}

class ApiError extends Error {
  constructor(status, code) {
    super(code || `http ${status}`);
    this.status = status;
    this.code = code;
  }
}

async function api(path, { method = "GET", body, timeout = 20000 } = {}) {
  const abort = new AbortController();
  const timer = setTimeout(() => abort.abort(), timeout);
  try {
    const resp = await fetch(`/api${path}`, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
      signal: abort.signal,
    });
    if (resp.status === 204) return null;
    const data = await resp.json().catch(() => null);
    if (!resp.ok) throw new ApiError(resp.status, data && data.error);
    return data;
  } catch (err) {
    throw err instanceof ApiError ? err : new ApiError(0, "network");
  } finally {
    clearTimeout(timer);
  }
}

const isActive = (item) => item && (item.state === "preparing" || item.state === "ready");

function gigabytes(bytes) {
  const gb = bytes / 1e9;
  return t("size_gb", { n: formatNumber(gb, gb < 10 ? 1 : 0) });
}

function runtimeText(minutes) {
  if (!minutes) return null;
  if (minutes < 60) return `${minutes} min`;
  return t("runtime", { h: Math.floor(minutes / 60), m: String(minutes % 60).padStart(2, "0") });
}

function poster(path, title, year, size = "") {
  if (!path) {
    return h("div", { class: `poster poster--empty ${size}`, "aria-hidden": "true" },
      h("span", { class: "poster-empty-title", text: title }),
      year ? h("span", { class: "poster-empty-year", text: String(year) }) : null);
  }
  return h("div", { class: `poster ${size}` },
    h("img", { src: POSTER + path, alt: "", loading: "lazy", decoding: "async" }));
}

const ICONS = {
  back: "M15 5 8 12l7 7",
  close: "M6 6l12 12M18 6 6 18",
  retry: "M20 12a8 8 0 1 1-2.34-5.66M20 4v5h-5",
};

function icon(name, cls = "icon") {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("class", cls);
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", ICONS[name]);
  svg.append(path);
  return svg;
}

function spinner() {
  return h("span", { class: "spinner", "aria-hidden": "true" });
}

function errorBlock(retry) {
  return h("button", { class: "error-block", type: "button", onclick: retry },
    h("span", { text: t("error") }),
    icon("retry"));
}

/** The family-facing status of a movie on the server. */
function statusLines(item) {
  const lines = [];
  if (item.state === "preparing") {
    lines.push(h("p", { class: "status status--preparing" }, spinner(), t("preparing")));
  } else if (item.state === "ready") {
    lines.push(h("p", { class: "status status--ready" }, t("ready"),
      item.may_buffer ? h("span", { class: "status-note", text: ` — ${t("may_buffer")}` }) : null));
    lines.push(h("p", { class: "status-sub", text: downloadingLine(item.percent, item.eta_s) }));
  } else if (item.state === "done") {
    lines.push(h("p", { class: "status status--done", text: t("done") }));
  } else if (item.state === "failed") {
    lines.push(h("p", { class: "status status--failed", text: t("failed") }));
  }
  if (isActive(item)) {
    lines.push(h("div", { class: "progress", role: "progressbar", "aria-valuemin": "0",
      "aria-valuemax": "100", "aria-valuenow": String(item.percent) },
      h("div", { class: "progress-fill", style: `width:${item.percent}%` })));
  }
  return lines;
}

/** Repeats `tick` every POLL_MS while it returns true; skips ticks while the app is hidden. */
function poller(tick) {
  let timer = null;
  let stopped = false;
  const schedule = () => {
    if (!stopped) timer = setTimeout(run, POLL_MS);
  };
  async function run() {
    if (document.hidden) return schedule();
    let again = true;
    try {
      again = await tick();
    } catch {
      again = true; // a dropped poll is retried on the next tick
    }
    if (again) schedule();
  }
  return {
    start() { clearTimeout(timer); schedule(); },
    stop() { stopped = true; clearTimeout(timer); },
  };
}

// ---------------------------------------------------------------- routing

function parseRoute() {
  const hash = location.hash.replace(/^#/, "") || "/";
  const [path, query = ""] = hash.split("?");
  const movie = path.match(/^\/movie\/(\d+)(\/more)?$/);
  if (movie) return { name: "movie", id: Number(movie[1]), sheet: Boolean(movie[2]) };
  if (path === "/search") {
    const q = (new URLSearchParams(query).get("q") || "").trim();
    if (q) return { name: "search", q };
  }
  return { name: "home" };
}

let page = null;
let renderedHash = null;
let cameBack = false; // the current render comes from the back/forward buttons
const scrollMemory = new Map();

function go(href, { replace = false } = {}) {
  cameBack = false;
  if (replace) history.replaceState(history.state, "", href);
  else history.pushState({ inApp: true }, "", href);
  render();
}

function goBack(fallback) {
  if (history.state && history.state.inApp) history.back();
  else go(fallback, { replace: true });
}

function render() {
  if (location.hash === renderedHash) return;
  renderedHash = location.hash;
  const route = parseRoute();
  if (page && page.route.name === "movie" && route.name === "movie" && page.route.id === route.id) {
    page.route = route;
    page.syncSheet();
    return;
  }
  if (page) {
    scrollMemory.set(page.key, window.scrollY);
    page.dispose();
  }
  closeSheet();
  topbar.hidden = route.name === "movie";
  if (route.name !== "search") searchInput.value = "";
  const key = route.name === "movie" ? `movie/${route.id}` : route.name;
  const restoreScroll = () => window.scrollTo(0, (cameBack && scrollMemory.get(key)) || 0);
  if (route.name === "home") page = homePage(route, restoreScroll);
  else if (route.name === "search") page = searchPage(route, restoreScroll);
  else page = moviePage(route, restoreScroll);
  page.key = key;
}

addEventListener("popstate", () => { cameBack = true; render(); });
addEventListener("hashchange", () => { cameBack = true; render(); });

document.addEventListener("click", (event) => {
  const link = event.target.closest("a[href^='#/']");
  if (!link || event.defaultPrevented || event.metaKey || event.ctrlKey) return;
  event.preventDefault();
  go(link.getAttribute("href"));
});

// ---------------------------------------------------------------- search box

let searchTimer = null;

function searchFor(text) {
  const q = text.trim();
  const target = q ? `#/search?q=${encodeURIComponent(q)}` : "#/";
  if (target === location.hash || (!q && parseRoute().name === "home")) return;
  go(target, { replace: parseRoute().name === "search" });
}

searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => searchFor(searchInput.value), SEARCH_DELAY_MS);
});

searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  clearTimeout(searchTimer);
  searchFor(searchInput.value);
  searchInput.blur();
});

// ---------------------------------------------------------------- home

let homeCache = null;

function posterGrid(cards) {
  return h("div", { class: "grid" }, cards.map((card) =>
    h("a", { class: "card", href: `#/movie/${card.id}`,
      "aria-label": card.year ? `${card.title} (${card.year})` : card.title },
      poster(card.poster_path, card.title, card.year),
      card.in_library ? h("span", { class: "card-badge", title: t("on_server") }) : null)));
}

function libraryRow(item) {
  return h("a", { class: "lib-row", href: `#/movie/${item.tmdb_id}` },
    poster(item.poster_path, item.title, item.year, "poster--thumb"),
    h("div", { class: "lib-text" },
      h("p", { class: "lib-title" }, item.title,
        item.year ? h("span", { class: "lib-year", text: ` ${item.year}` }) : null),
      statusLines(item)));
}

function homePage(route, restoreScroll) {
  const librarySection = h("section", { class: "section", hidden: true });
  const catalog = h("div");
  const footer = h("footer", { class: "footer" });
  let alive = true;
  let libraryJson = null;

  function paintLibrary(data) {
    const json = JSON.stringify(data.library);
    if (json === libraryJson) return;
    libraryJson = json;
    librarySection.hidden = data.library.length === 0;
    librarySection.replaceChildren(
      h("h2", { class: "section-title", text: t("on_server") }),
      h("div", { class: "lib-list" }, data.library.map(libraryRow)));
  }

  function paintFooter(data) {
    footer.textContent = data.disk
      ? t("free_space", { n: formatNumber(Math.round(data.disk.free_bytes / 1e9)) }) : "";
  }

  function paint(data) {
    paintLibrary(data);
    paintFooter(data);
    if (data.catalog_error) {
      catalog.replaceChildren(errorBlock(() => refresh()));
    } else {
      catalog.replaceChildren(...[
        data.trending.length ? h("section", { class: "section" },
          h("h2", { class: "section-title", text: t("trending") }), posterGrid(data.trending)) : null,
        data.popular.length ? h("section", { class: "section" },
          h("h2", { class: "section-title", text: t("popular") }), posterGrid(data.popular)) : null,
      ].filter(Boolean));
    }
  }

  async function refresh() {
    try {
      const data = await api("/home");
      if (!alive) return;
      homeCache = data;
      paint(data);
      if (data.library.some(isActive)) poll.start();
    } catch {
      if (alive && !homeCache) catalog.replaceChildren(errorBlock(() => refresh()));
    }
  }

  const poll = poller(async () => {
    const data = await api("/home");
    if (!alive) return false;
    homeCache = { ...homeCache, library: data.library, disk: data.disk };
    paintLibrary(data);
    paintFooter(data);
    return data.library.some(isActive);
  });

  view.replaceChildren(h("div", { class: "home" }, librarySection, catalog, footer));
  if (homeCache) {
    paint(homeCache);
    restoreScroll();
  } else {
    window.scrollTo(0, 0);
    catalog.replaceChildren(h("div", { class: "loading" }, spinner()));
  }
  refresh();

  return { route, syncSheet() {}, dispose() { alive = false; poll.stop(); } };
}

// ---------------------------------------------------------------- search

function searchPage(route, restoreScroll) {
  let alive = true;
  searchInput.value = route.q;
  if (!view.querySelector(".results")) {
    view.replaceChildren(h("div", { class: "results" }, h("div", { class: "loading" }, spinner())));
  }
  const show = (...children) => {
    view.replaceChildren(h("div", { class: "results" }, ...children));
  };

  async function run() {
    try {
      const data = await api(`/search?q=${encodeURIComponent(route.q)}`);
      if (!alive) return;
      show(data.results.length ? posterGrid(data.results)
        : h("p", { class: "empty", text: t("no_results") }));
      restoreScroll();
    } catch {
      if (alive) show(errorBlock(run));
    }
  }
  run();

  return { route, syncSheet() {}, dispose() { alive = false; } };
}

// ---------------------------------------------------------------- movie

function moviePage(route, restoreScroll) {
  const id = route.id;
  const state = {
    movie: null,
    library: null,
    releases: null, // {data, at}
    finding: false,
    message: null, // "none_found" | "error"
    sheetPick: null,
    sheetBusy: false,
    sheetError: false,
  };
  let alive = true;
  const actions = h("div", { class: "actions", "aria-live": "polite" });
  const page = { route, key: null, syncSheet, dispose };

  window.scrollTo(0, 0);
  view.replaceChildren(h("div", { class: "movie" },
    backButton(), h("div", { class: "loading loading--tall" }, spinner())));

  function backButton() {
    return h("button", { class: "back", type: "button", "aria-label": t("back"),
      onclick: () => goBack("#/") },
      icon("back"));
  }

  async function load() {
    try {
      const data = await api(`/movies/${id}`);
      if (!alive) return;
      state.movie = data.movie;
      state.library = data.library;
      paintPage();
      restoreScroll();
      if (isActive(state.library)) poll.start();
      syncSheet();
    } catch {
      if (!alive) return;
      view.replaceChildren(h("div", { class: "movie" }, backButton(),
        h("div", { class: "movie-body movie-body--bare" }, errorBlock(load))));
    }
  }

  function paintPage() {
    const m = state.movie;
    const art = m.backdrop_path ? BACKDROP + m.backdrop_path : m.poster_path ? POSTER + m.poster_path : null;
    const meta = [m.year, runtimeText(m.runtime)].filter(Boolean);
    document.title = m.title;
    view.replaceChildren(h("article", { class: "movie" },
      h("div", { class: `backdrop${art ? "" : " backdrop--empty"}` },
        art ? h("img", { src: art, alt: "", decoding: "async" }) : null),
      backButton(),
      h("div", { class: "movie-body" },
        h("div", { class: "movie-head" },
          poster(m.poster_path, m.title, m.year, "poster--hero"),
          h("div", { class: "movie-heading" },
            h("h1", { class: "movie-title", text: m.title }),
            meta.length ? h("p", { class: "movie-meta" },
              meta.map((part) => h("span", { text: String(part) }))) : null)),
        actions,
        m.overview ? h("p", { class: "overview", text: m.overview }) : null)));
    paintActions();
  }

  function paintActions() {
    const item = state.library;
    const children = [];
    if (item) {
      children.push(h("div", { class: "status-block" }, statusLines(item)));
      if (item.state === "failed") {
        children.push(h("button", { class: "btn btn--primary", type: "button",
          onclick: openSheet, text: t("more") }));
      }
      children.push(h("button", { class: "btn btn--quiet", type: "button",
        onclick: remove, text: t("remove") }));
    } else if (state.finding) {
      children.push(h("div", { class: "btn btn--primary btn--busy", role: "status" },
        spinner(), t("finding")));
      children.push(h("button", { class: "btn btn--secondary", type: "button", disabled: true,
        text: t("more") }));
    } else {
      children.push(h("button", { class: "btn btn--primary", type: "button",
        onclick: getRecommended, text: t("recommended") }));
      children.push(h("button", { class: "btn btn--secondary", type: "button",
        onclick: openSheet, text: t("more") }));
    }
    if (state.message) {
      children.push(h("p", { class: `message message--${state.message}`, text: t(state.message) }));
    }
    actions.replaceChildren(...children);
  }

  async function releases({ fresh = false } = {}) {
    const cached = state.releases;
    if (!fresh && cached && Date.now() - cached.at < RELEASES_FRESH_MS) return cached.data;
    const data = await api(`/movies/${id}/releases`, { timeout: 90000 });
    state.releases = { data, at: Date.now() };
    return data;
  }

  /** POST a release. Returns "ok", "expired" (release list must be refetched) or throws. */
  async function startDownload(releaseId) {
    try {
      state.library = await api(`/movies/${id}/download`, {
        method: "POST", body: { release_id: releaseId }, timeout: 60000 });
    } catch (err) {
      if (err.status === 410) {
        state.releases = null;
        return "expired";
      }
      if (err.status !== 409) throw err;
      state.library = (await api(`/movies/${id}`)).library; // someone else got it first
    }
    if (isActive(state.library)) poll.start();
    return "ok";
  }

  async function getRecommended() {
    state.finding = true;
    state.message = null;
    paintActions();
    try {
      let best = (await releases()).recommended;
      let outcome = best ? await startDownload(best.id) : "none";
      if (outcome === "expired") {
        best = (await releases({ fresh: true })).recommended;
        outcome = best ? await startDownload(best.id) : "none";
      }
      if (outcome === "none") state.message = "none_found";
      else if (outcome === "expired") state.message = "error";
    } catch {
      state.message = "error";
    }
    state.finding = false;
    if (alive) paintActions();
  }

  async function remove() {
    if (!window.confirm(t("remove_confirm"))) return;
    state.message = null;
    try {
      await api(`/movies/${id}`, { method: "DELETE", timeout: 30000 });
      state.library = null;
      state.releases = null;
    } catch {
      state.message = "error";
    }
    if (alive) paintActions();
  }

  const poll = poller(async () => {
    const data = await api("/home");
    if (!alive) return false;
    const item = data.library.find((entry) => entry.tmdb_id === id) || null;
    if (JSON.stringify(item) !== JSON.stringify(state.library)) {
      state.library = item;
      paintActions();
    }
    return isActive(item);
  });

  // ---- the More sheet (its own history entry, so the phone's back gesture closes it)

  function openSheet() {
    state.sheetPick = null;
    state.sheetError = false;
    go(`#/movie/${id}/more`);
  }

  function dismissSheet() {
    if (history.state && history.state.inApp) history.back();
    else go(`#/movie/${id}`, { replace: true });
  }

  function syncSheet() {
    if (!state.movie) return; // opened once the movie has loaded
    if (!page.route.sheet || (state.library && state.library.state !== "failed")) {
      closeSheet();
      return;
    }
    showSheet(dismissSheet);
    paintSheet();
    if (!state.releases) loadSheetReleases({});
  }

  async function loadSheetReleases({ fresh = false }) {
    try {
      await releases({ fresh });
      state.sheetError = false;
    } catch {
      state.sheetError = true;
    }
    if (alive && page.route.sheet) paintSheet();
  }

  function releaseRow(rel) {
    const picked = state.sheetPick === rel.id;
    const facts = [
      rel.source !== "other" ? t(rel.source) : null,
      rel.sound === "atmos" ? t("atmos") : rel.sound,
      t(`lang_${rel.lang}`),
    ].filter(Boolean);
    return h("button", { class: `release${picked ? " release--picked" : ""}`, type: "button",
      "aria-pressed": picked ? "true" : "false",
      onclick: () => { state.sheetPick = picked ? null : rel.id; state.sheetError = false; paintSheet(); } },
      h("span", { class: "release-size", text: gigabytes(rel.size_bytes) }),
      h("span", { class: "release-facts" }, facts.map((f) => h("span", { class: "fact", text: f }))),
      h("span", { class: `speed speed--${rel.speed}`, text: t(`speed_${rel.speed}`) }));
  }

  function paintSheet() {
    const cached = state.releases && state.releases.data;
    const body = h("div", { class: "sheet-body" });
    if (cached) {
      for (const group of cached.groups) {
        body.append(h("section", { class: "group" },
          h("h3", { class: "group-title", text: `${group.resolution}p` }),
          group.standard.length ? h("div", { class: "subgroup" },
            h("h4", { class: "subgroup-title", text: t("standard") }),
            group.standard.map(releaseRow)) : null,
          group.atmos.length ? h("div", { class: "subgroup" },
            h("h4", { class: "subgroup-title", text: t("atmos") }),
            group.atmos.map(releaseRow)) : null));
      }
      if (!cached.groups.length) body.append(h("p", { class: "empty", text: t("none_found") }));
    } else if (state.sheetError) {
      body.append(errorBlock(() => { state.sheetError = false; paintSheet(); loadSheetReleases({}); }));
    } else {
      body.append(h("p", { class: "finding" }, spinner(), t("finding")));
    }

    const footer = state.sheetPick ? h("div", { class: "sheet-footer" },
      state.sheetError && cached ? h("p", { class: "message message--error", text: t("error") }) : null,
      h("button", { class: "btn btn--primary", type: "button", disabled: state.sheetBusy,
        onclick: confirmPick }, state.sheetBusy ? spinner() : null, t("get_it"))) : null;

    sheet.setAttribute("aria-label", t("more"));
    const scroll = sheet.querySelector(".sheet-body")?.scrollTop || 0;
    sheet.replaceChildren(...[
      h("div", { class: "sheet-head" },
        h("h2", { class: "sheet-title", text: state.movie.title }),
        h("button", { class: "icon-btn", type: "button", "aria-label": t("close"),
          onclick: dismissSheet }, icon("close"))),
      body, footer].filter(Boolean));
    body.scrollTop = scroll;
  }

  async function confirmPick() {
    state.sheetBusy = true;
    paintSheet();
    try {
      const outcome = await startDownload(state.sheetPick);
      state.sheetBusy = false;
      if (!alive) return;
      if (outcome === "expired") {
        state.sheetPick = null;
        paintSheet();
        await loadSheetReleases({ fresh: true });
        return;
      }
      state.message = null;
      paintActions();
      dismissSheet();
    } catch {
      state.sheetBusy = false;
      state.sheetError = true;
      if (alive) paintSheet();
    }
  }

  function dispose() {
    alive = false;
    poll.stop();
    document.title = "Cinema";
  }

  load();
  return page;
}

// ---------------------------------------------------------------- sheet layer

let onSheetDismiss = null;

function showSheet(onDismiss) {
  onSheetDismiss = onDismiss;
  if (!sheetLayer.hidden) return;
  sheetLayer.hidden = false;
  document.body.classList.add("sheet-open");
  requestAnimationFrame(() => sheetLayer.classList.add("sheet-layer--open"));
}

function closeSheet() {
  onSheetDismiss = null;
  sheetLayer.classList.remove("sheet-layer--open");
  sheetLayer.hidden = true;
  document.body.classList.remove("sheet-open");
}

sheetLayer.addEventListener("click", (event) => {
  if (event.target.closest("[data-close-sheet]") && onSheetDismiss) onSheetDismiss();
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && onSheetDismiss) onSheetDismiss();
});

render();
