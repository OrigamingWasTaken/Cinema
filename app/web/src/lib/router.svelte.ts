// A tiny hash router. Tabs and the film page live in the URL, so the phone's back
// button (and the iOS swipe) behave like in a native app.
//
//   #/            Home           #/movie/123       film page, over the current tab
//   #/search?q=   Search         #/movie/123/more  the More versions sheet
//   #/library     Library
//   #/downloads   Downloads
import { flushSync, tick } from "svelte";

export type Tab = "home" | "search" | "library" | "downloads";
export const TABS: Tab[] = ["home", "search", "library", "downloads"];

export interface Route {
  tab: Tab;
  movie: number | null;
  sheet: boolean;
  q: string;
}

/** What we keep in history.state: which tab a film page sits on, and how deep it is. */
interface Entry {
  inApp: true;
  tab: Tab;
  depth: number;
}

const TAB_PATHS: Record<Tab, string> = {
  home: "#/",
  search: "#/search",
  library: "#/library",
  downloads: "#/downloads",
};

export const tabHref = (tab: Tab) => TAB_PATHS[tab];

function parse(hash: string, fallbackTab: Tab): Route {
  const [path, query = ""] = (hash.replace(/^#/, "") || "/").split("?");
  const movie = path.match(/^\/movie\/(\d+)(\/more)?\/?$/);
  if (movie) return { tab: fallbackTab, movie: Number(movie[1]), sheet: Boolean(movie[2]), q: "" };
  const tab = (TABS.find((name) => name !== "home" && path === `/${name}`) ?? "home") as Tab;
  const q = tab === "search" ? (new URLSearchParams(query).get("q") ?? "") : "";
  return { tab, movie: null, sheet: false, q };
}

const entry = (): Partial<Entry> => (history.state && history.state.inApp ? history.state : {});

export const router = $state<{ route: Route }>({
  route: parse(location.hash, (entry().tab as Tab) ?? "home"),
});

/** The view whose scroll position we remember: a tab, or one film page. */
const viewKey = (r: Route) => (r.movie != null ? `movie/${r.movie}` : r.tab);
const scrollMemory = new Map<string, number>();

async function apply(next: Route, popped: boolean) {
  const before = viewKey(router.route);
  scrollMemory.set(before, window.scrollY);
  router.route = next;
  const after = viewKey(next);
  if (after === before) return;
  await tick();
  // Tabs always come back where you left them; a film page starts at the top
  // unless you return to it with the back button.
  const keep = next.movie == null || popped;
  window.scrollTo(0, keep ? (scrollMemory.get(after) ?? 0) : 0);
}

export function navigate(href: string, { replace = false } = {}) {
  const current = entry();
  const next = parse(href, router.route.tab);
  const depth = next.movie == null ? 0 : (current.depth ?? 0) + (replace ? 0 : 1);
  const state: Entry = { inApp: true, tab: next.tab, depth: next.movie == null ? 0 : depth };
  if (replace) history.replaceState(state, "", href);
  else history.pushState(state, "", href);
  apply(next, false);
}

/** Back within the app, or to `fallback` when the page was opened from a link. */
export function goBack(fallback: string) {
  if (entry().inApp && (entry().depth ?? 0) > 0) history.back();
  else navigate(fallback, { replace: true });
}

/** A tab bar tap: switch tabs, pop a film page off the tab, or scroll to the top. */
export function selectTab(tab: Tab) {
  const { route } = router;
  if (route.tab === tab && route.movie == null) {
    window.scrollTo({ top: 0, behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    return;
  }
  if (route.tab === tab && route.movie != null) {
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
  apply(parse(location.hash, state?.tab ?? router.route.tab), true);
});

// A hash typed by hand (or an old bookmark) arrives without our history state.
addEventListener("hashchange", () => {
  const next = parse(location.hash, router.route.tab);
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
