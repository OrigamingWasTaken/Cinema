// A tiny hash router. Tabs and the pages pushed over them live in the URL, so the
// phone's back button (and the iOS swipe) behave like in a native app.
//
//   #/            Films          #/movie/123       film page, over the current tab
//   #/shows       Shows          #/movie/123/more  the More versions sheet
//   #/search?q=   Search         #/show/1396       show page, over the current tab
//   #/library     Library        #/person/31       a person's films, over the current tab
//   #/downloads   Downloads      #/studio/3        (also #/collection/10, #/genre/16)
//
// A film opened from one of those pages sits over it: back returns to that page.
import { flushSync, tick } from "svelte";

export type Tab = "films" | "shows" | "search" | "library" | "downloads";
export const TABS: Tab[] = ["films", "shows", "search", "library", "downloads"];

export type BrowseKind = "person" | "studio" | "collection" | "genre";

/** A page of films (a person, a studio, a collection or a genre). */
export interface Browse {
  kind: BrowseKind;
  id: number;
}

export interface Route {
  tab: Tab;
  /** The browse page shown, or the one under the film page. */
  browse: Browse | null;
  movie: number | null;
  /** A show page (TMDB id), pushed over the tab like a film page. */
  show: number | null;
  sheet: boolean;
  q: string;
}

/** Where a pushed page sits: its tab, and the browse page under a film page. */
type Base = Pick<Route, "tab" | "browse">;

/** What we keep in history.state: where a pushed page sits, and how deep it is. */
interface Entry extends Base {
  inApp: true;
  depth: number;
}

const TAB_PATHS: Record<Tab, string> = {
  films: "#/",
  shows: "#/shows",
  search: "#/search",
  library: "#/library",
  downloads: "#/downloads",
};

export const tabHref = (tab: Tab) => TAB_PATHS[tab];
export const browseHref = (page: Browse) => `#/${page.kind}/${page.id}`;

/** A tab itself, with nothing pushed over it. */
export const onTab = (r: Route) => r.movie == null && r.show == null && r.browse == null;

function parse(hash: string, base: Base): Route {
  const [path, query = ""] = (hash.replace(/^#/, "") || "/").split("?");
  const movie = path.match(/^\/movie\/(\d+)(\/more)?\/?$/);
  if (movie) return { ...base, movie: Number(movie[1]), show: null, sheet: Boolean(movie[2]), q: "" };
  const show = path.match(/^\/show\/(\d+)\/?$/);
  if (show) return { ...base, movie: null, show: Number(show[1]), sheet: false, q: "" };
  const page = path.match(/^\/(person|studio|collection|genre)\/(\d+)\/?$/);
  if (page) {
    const browse = { kind: page[1] as BrowseKind, id: Number(page[2]) };
    return { tab: base.tab, browse, movie: null, show: null, sheet: false, q: "" };
  }
  const tab = TABS.find((name) => name !== "films" && path === `/${name}`) ?? "films";
  const q = tab === "search" ? (new URLSearchParams(query).get("q") ?? "") : "";
  return { tab, browse: null, movie: null, show: null, sheet: false, q };
}

// Entries saved by an older version of the app may name a tab that no longer exists.
const knownTab = (tab: unknown): Tab | undefined => TABS.find((name) => name === tab);
const entry = (): Partial<Entry> =>
  history.state && history.state.inApp ? { ...history.state, tab: knownTab(history.state.tab) } : {};
// A plain copy: history.state cannot hold the route's reactive proxy.
const baseOf = (r: Route): Base => ({ tab: r.tab, browse: r.browse && { kind: r.browse.kind, id: r.browse.id } });

// A page opened from a link belongs with the tab it comes from: a browse page with
// Search, a show with Shows, a film with Films.
const startTab: Tab = entry().tab ??
  (/^#\/(person|studio|collection|genre)\//.test(location.hash) ? "search"
    : /^#\/show\//.test(location.hash) ? "shows" : "films");

export const router = $state<{ route: Route }>({
  route: parse(location.hash, { tab: startTab, browse: entry().browse ?? null }),
});

/** The view whose scroll position we remember: a tab, a browse page, one film or show page. */
const viewKey = (r: Route) =>
  r.movie != null ? `movie/${r.movie}`
    : r.show != null ? `show/${r.show}`
      : r.browse ? `${r.browse.kind}/${r.browse.id}` : r.tab;
const scrollMemory = new Map<string, number>();

async function apply(next: Route, popped: boolean) {
  const before = viewKey(router.route);
  scrollMemory.set(before, window.scrollY);
  router.route = next;
  const after = viewKey(next);
  if (after === before) return;
  await tick();
  // Tabs always come back where you left them; a pushed page starts at the top
  // unless you return to it with the back button.
  const keep = onTab(next) || popped;
  window.scrollTo(0, keep ? (scrollMemory.get(after) ?? 0) : 0);
}

export function navigate(href: string, { replace = false } = {}) {
  const current = entry();
  const next = parse(href, baseOf(router.route));
  const depth = onTab(next) ? 0 : (current.depth ?? 0) + (replace ? 0 : 1);
  const state: Entry = { inApp: true, tab: next.tab, browse: next.browse, depth };
  if (replace) history.replaceState(state, "", href);
  else history.pushState(state, "", href);
  apply(next, false);
}

/** Back within the app, or to `fallback` when the page was opened from a link. */
export function goBack(fallback: string) {
  if (entry().inApp && (entry().depth ?? 0) > 0) history.back();
  else navigate(fallback, { replace: true });
}

/** A tab bar tap: switch tabs, pop the pages pushed over the tab, or scroll to the top. */
export function selectTab(tab: Tab) {
  const { route } = router;
  if (route.tab === tab && onTab(route)) {
    window.scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    return;
  }
  if (route.tab === tab) {
    const depth = entry().depth ?? 0;
    if (entry().inApp && depth > 0) {
      history.go(-depth);
      return;
    }
  }
  // Search remembers its query in the URL.
  const href = tab === "search" && searchQuery ? `${TAB_PATHS.search}?q=${encodeURIComponent(searchQuery)}` : TAB_PATHS[tab];
  navigate(href);
  flushSync();
}

let searchQuery = "";
/** Search keeps its last query so switching tabs and back restores it. */
export function rememberQuery(q: string) {
  searchQuery = q;
}

addEventListener("popstate", (event) => {
  const state = event.state && event.state.inApp ? (event.state as Entry) : null;
  const base = state ? { tab: knownTab(state.tab) ?? "films", browse: state.browse ?? null } : baseOf(router.route);
  apply(parse(location.hash, base), true);
});

// A hash typed by hand (or an old bookmark) arrives without our history state.
addEventListener("hashchange", () => {
  const next = parse(location.hash, baseOf(router.route));
  if (JSON.stringify(next) !== JSON.stringify(router.route)) apply(next, true);
});

/** Same-app links (<a href="#/movie/1">) go through navigate() so history stays ours. */
document.addEventListener("click", (event) => {
  if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey) return;
  const link = (event.target as Element | null)?.closest?.("a[href^='#/']");
  if (!link) return;
  event.preventDefault();
  navigate(link.getAttribute("href")!);
});
