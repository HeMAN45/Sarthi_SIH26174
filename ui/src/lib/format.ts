/** Formatting shared by every screen, so a number reads the same everywhere. */

export function fmtBytes(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

/** Mission-elapsed clock: 00:04:12. */
export function fmtClock(seconds: number | null | undefined): string {
  const s = Math.max(0, Math.floor(seconds ?? 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (v: number) => String(v).padStart(2, "0");
  return `${pad(h)}:${pad(m)}:${pad(s % 60)}`;
}

/** Human duration: 42s · 3m 12s · 1h 04m. */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "-";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${String(s % 60).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`;
}

/** Step timing keeps a decimal under a minute: 12.4s reads better than 12s. */
export function fmtStep(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return "-";
  return seconds < 60 ? `${seconds.toFixed(1)}s` : fmtDuration(seconds);
}

export function fmtRatio(n: number): string {
  if (n >= 10000) return `${Math.round(n / 1000)}k`;
  return n.toLocaleString("en-US");
}

const dateFmt = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
});

export function fmtWhen(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : dateFmt.format(d);
}

/** First and last eight characters: enough to compare, short enough to read. */
export function shortHash(h: string | null | undefined): [string, string] {
  if (!h) return ["", ""];
  return h.length <= 20 ? [h, ""] : [h.slice(0, 8), h.slice(-8)];
}

export function plural(n: number, word: string, many = `${word}s`): string {
  return `${n} ${n === 1 ? word : many}`;
}
