/** Formatting for times, seasons and counts. Times are Unix seconds from the API. */

const timeFormat = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit" });
const dateFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export function timeAgo(seconds: number | null | undefined): string {
  if (!seconds) return "never";
  const then = new Date(seconds * 1000);
  const now = new Date();
  const diff = (now.getTime() - then.getTime()) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)} min ago`;
  if (sameDay(then, now)) return `today ${timeFormat.format(then)}`;
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(then, yesterday)) return `yesterday ${timeFormat.format(then)}`;
  return `${dateFormat.format(then)} ${timeFormat.format(then)}`;
}

export function dateOnly(seconds: number | null | undefined): string {
  return seconds ? dateFormat.format(new Date(seconds * 1000)) : "";
}

export function seasonLabel(n: number | null | undefined): string {
  if (n === null || n === undefined) return "Show";
  return n === 0 ? "Specials" : `Season ${n}`;
}

export function seasonShort(n: number): string {
  return n === 0 ? "Sp" : String(n);
}

export function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Caption under a set image: "Show", "Movie", "S2", "Specials". */
export function imageCaption(image: { kind: string | null; season: number | null }): string {
  if (image.kind === "season") return image.season === 0 ? "Specials" : `S${image.season}`;
  if (image.kind === "show") return "Show";
  if (image.kind === "movie") return "Movie";
  return "";
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return url;
  }
}
