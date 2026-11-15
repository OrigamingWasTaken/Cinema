// Browser playback helpers: this device's id, resume positions, and how to play a file.
// Kept free of imports so the smoke harness can run it under plain Node.

/** /api/play/movie/{id} and /api/play/episode/{show}/{season}/{episode}. */
export interface PlayInfo {
  title: string;
  /** "S01E02" for an episode, null for a film. */
  subtitle: string | null;
  duration: number;
  container: string;
  video_codec: string;
  /** What canPlayType / isTypeSupported need, e.g. "avc1.640028", "hvc1.2.4.L120.90". */
  codec_string: string;
  audio_codec: string | null;
  /** The file itself (mp4/m4v only), served with byte ranges. */
  direct_url: string | null;
  hls_url: string;
}

export type Source = "direct" | "hls" | "unsupported";

// Storage can be missing or throw (private mode, blocked site data): fall back to memory.
const memory = new Map<string, string>();

function read(key: string): string | null {
  try {
    const value = localStorage.getItem(key);
    if (value != null) return value;
  } catch {
    // fall through to memory
  }
  return memory.get(key) ?? null;
}

function write(key: string, value: string) {
  memory.set(key, value);
  try {
    localStorage.setItem(key, value);
  } catch {
    // memory only
  }
}

function forget(key: string) {
  memory.delete(key);
  try {
    localStorage.removeItem(key);
  } catch {
    // memory only
  }
}

const DEVICE_KEY = "cinema-device";
const DEVICE_RE = /^[A-Za-z0-9-]{8,64}$/;

// crypto.randomUUID only exists on https and localhost; the app runs on plain http.
function uuid(): string {
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const hex = [...b].map((n) => n.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** A random id for this browser, sent with HLS requests so the server can tell viewers apart. */
export function deviceId(): string {
  const known = read(DEVICE_KEY);
  if (known && DEVICE_RE.test(known)) return known;
  const id = uuid();
  write(DEVICE_KEY, id);
  return id;
}

const resumeKey = (item: string) => `cinema-resume:${item}`;
/** Nothing to resume before this many seconds. */
const MIN_RESUME_S = 5;
/** The last 3 % (end credits) counts as finished. */
const finished = (t: number, duration: number) => duration > 0 && t >= duration * 0.97;

/** Where `item` was left on this device, or null (never started, or finished). */
export function savedPosition(item: string, duration: number): number | null {
  const t = Number(read(resumeKey(item)));
  if (!Number.isFinite(t) || t < MIN_RESUME_S) return null;
  if (finished(t, duration)) {
    clearPosition(item);
    return null;
  }
  return t;
}

export function savePosition(item: string, t: number, duration: number) {
  if (!Number.isFinite(t) || t < MIN_RESUME_S || finished(t, duration)) clearPosition(item);
  else write(resumeKey(item), String(Math.floor(t)));
}

export function clearPosition(item: string) {
  forget(resumeKey(item));
}

/** "42:10", "1:02:03". */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const two = (n: number) => String(n).padStart(2, "0");
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}:${two(m)}:${two(s % 60)}` : `${m}:${two(s % 60)}`;
}

// The codec string browsers use for the file's own audio, when it can stay as it is.
const AUDIO: Record<string, string> = { aac: "mp4a.40.2", ac3: "ac-3", eac3: "ec-3" };

export const nativeHls = (video: Pick<HTMLVideoElement, "canPlayType">) =>
  video.canPlayType("application/vnd.apple.mpegurl") !== "";

function mseSupports(type: string): boolean {
  const g = globalThis as { MediaSource?: typeof MediaSource; ManagedMediaSource?: typeof MediaSource };
  const mse = g.MediaSource ?? g.ManagedMediaSource;
  try {
    return !!mse && mse.isTypeSupported(type);
  } catch {
    return false;
  }
}

/**
 * Whether the HLS rendition (video copied, audio made AAC) plays here: with native HLS
 * the browser must also take the video codec in MP4 (else HEVC ends in a black screen),
 * and without it hls.js needs Media Source for that codec.
 */
export function hlsSupported(info: PlayInfo, video: Pick<HTMLVideoElement, "canPlayType">): boolean {
  const mse = mseSupports(`video/mp4; codecs="${info.codec_string}"`);
  if (!nativeHls(video)) return mse;
  return video.canPlayType(`video/mp4; codecs="${info.codec_string}, mp4a.40.2"`) !== "" || mse;
}

/**
 * The file as it is when the browser plays its exact codecs, else the HLS rendition,
 * else nothing.
 */
export function chooseSource(info: PlayInfo, video: Pick<HTMLVideoElement, "canPlayType">): Source {
  if (info.direct_url) {
    const audio = info.audio_codec ? AUDIO[info.audio_codec] : null;
    const codecs = info.audio_codec == null ? info.codec_string : audio ? `${info.codec_string}, ${audio}` : null;
    if (codecs && video.canPlayType(`video/mp4; codecs="${codecs}"`) !== "") return "direct";
  }
  return hlsSupported(info, video) ? "hls" : "unsupported";
}

/**
 * What a <video> error means. MEDIA_ERR_DECODE (3) or MEDIA_ERR_SRC_NOT_SUPPORTED (4)
 * before anything played is the codec: the can't-play line, not a retry.
 */
export function mediaFailure(code: number | undefined, started: boolean): "unsupported" | "error" {
  return !started && (code === 3 || code === 4) ? "unsupported" : "error";
}

export type Preflight = "ready" | "preparing" | "busy" | "error";

/** The playlist's answer: 503 {"error":"preparing"} while the server indexes a long file. */
export function playlistOutcome(status: number, body: string): Preflight {
  if (status >= 200 && status < 300) return "ready";
  if (status === 429) return "busy";
  if (status === 503) {
    try {
      if (JSON.parse(body)?.error === "preparing") return "preparing";
    } catch {
      // a proxy's own 503
    }
  }
  return "error";
}

/** Retry-After in ms, kept between 1 and 30 s (3 s when missing). */
export function retryAfterMs(header: string | null): number {
  const s = Number(header ?? "");
  if (header == null || header.trim() === "" || !Number.isFinite(s)) return 3000;
  return Math.min(30, Math.max(1, s)) * 1000;
}

/** A signal that aborts after `ms` or with `signal`; clear() once the request is done. */
export function withTimeout(signal: AbortSignal, ms: number): { signal: AbortSignal; clear: () => void } {
  const ctl = new AbortController();
  const stop = () => ctl.abort();
  const timer = setTimeout(stop, ms);
  if (signal.aborted) ctl.abort();
  else signal.addEventListener("abort", stop, { once: true });
  return {
    signal: ctl.signal,
    clear: () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", stop);
    },
  };
}
