// Getting a film: find the recommended version and ask the server for it.
// Shared by the Home hero and the film page, so both show the same progress.
import { api, ApiError, type LibraryItem, type MovieData, type ReleasesData } from "./api";
import { catalog } from "./catalog.svelte";

const RELEASES_FRESH_MS = 5 * 60 * 1000; // the server keeps release ids for 10 min

export type Message = "none_found" | "error";

interface Flow {
  finding: boolean;
  message: Message | null;
}

const flows = $state<Record<number, Flow>>({});
const releaseCache = new Map<number, { data: ReleasesData; at: number }>();

export function flow(id: number): Flow {
  return flows[id] ?? { finding: false, message: null };
}

function update(id: number, patch: Partial<Flow>) {
  flows[id] = { ...flow(id), ...patch };
}

export function clearMessage(id: number) {
  if (flows[id]?.message) update(id, { message: null });
}

export function forgetReleases(id: number) {
  releaseCache.delete(id);
}

export function cachedReleases(id: number): ReleasesData | null {
  const hit = releaseCache.get(id);
  return hit && Date.now() - hit.at < RELEASES_FRESH_MS ? hit.data : null;
}

export async function releases(id: number, { fresh = false } = {}): Promise<ReleasesData> {
  const cached = fresh ? null : cachedReleases(id);
  if (cached) return cached;
  // Prowlarr searches every indexer; that can take a while.
  const data = await api<ReleasesData>(`/movies/${id}/releases`, { timeout: 90000 });
  releaseCache.set(id, { data, at: Date.now() });
  return data;
}

/** POST a release. "expired" means the release list must be fetched again. */
export async function startDownload(id: number, releaseId: string): Promise<"ok" | "expired"> {
  let item: LibraryItem | null;
  try {
    // The server may take resolve (30 s) + torrent metadata (120 s) + add (120 s).
    item = await api<LibraryItem>(`/movies/${id}/download`, {
      method: "POST", body: { release_id: releaseId }, timeout: 300000 });
  } catch (err) {
    const status = err instanceof ApiError ? err.status : 0;
    if (status === 410) {
      releaseCache.delete(id);
      return "expired";
    }
    if (status !== 409 && status !== 0) throw err;
    // 409: someone else got it first. 0 (timeout, network): the add may still have
    // gone through, so show the film's status if it is on the server now.
    item = (await api<MovieData>(`/movies/${id}`)).library;
    if (status === 0 && !item) throw err;
  }
  catalog.set(id, item);
  return "ok";
}

/** The one-tap path. Resolves to what the screen should do next. */
export async function getRecommended(id: number): Promise<"ok" | "none" | "error"> {
  if (flow(id).finding) return "error";
  update(id, { finding: true, message: null });
  let outcome: "ok" | "none" | "error" | "expired";
  try {
    let best = (await releases(id)).recommended;
    outcome = best ? await startDownload(id, best.id) : "none";
    if (outcome === "expired") {
      best = (await releases(id, { fresh: true })).recommended;
      outcome = best ? await startDownload(id, best.id) : "none";
    }
    if (outcome === "expired") outcome = "error";
  } catch {
    outcome = "error";
  }
  update(id, {
    finding: false,
    message: outcome === "none" ? "none_found" : outcome === "error" ? "error" : null,
  });
  return outcome;
}
