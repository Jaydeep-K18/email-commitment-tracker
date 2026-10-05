/**
 * Formatting dates, durations and numbers for people.
 *
 * Two kinds of time arrive from the API and they are handled differently on
 * purpose. Instants end in "Z" and are shown in the viewer's own time zone.
 * Deadlines have no zone — they are the wall-clock time the email named — so
 * they are shown exactly as written, never shifted.
 */

const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

const UNITS: Array<[Intl.RelativeTimeFormatUnit, number]> = [
  ["year", 365 * 24 * 3600],
  ["month", 30 * 24 * 3600],
  ["week", 7 * 24 * 3600],
  ["day", 24 * 3600],
  ["hour", 3600],
  ["minute", 60],
];

/** "3 minutes ago", "in 2 days". */
export function relative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const seconds = (Date.parse(iso) - now) / 1000;
  if (Math.abs(seconds) < 45) return "just now";
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return rtf.format(Math.round(seconds / size), unit);
  }
  return rtf.format(Math.round(seconds / 60), "minute");
}

/** An instant, in the viewer's zone: "12 Sep, 14:05". */
export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }).format(new Date(iso));
}

/** Inbox-style: time today, weekday this week, date otherwise. */
export function shortDate(iso: string | null | undefined, now = new Date()): string {
  if (!iso) return "";
  const date = new Date(iso);
  const sameDay = date.toDateString() === now.toDateString();
  if (sameDay) return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(date);
  const days = (now.getTime() - date.getTime()) / 86_400_000;
  if (days < 6 && days > 0) return new Intl.DateTimeFormat(undefined, { weekday: "short" }).format(date);
  return new Intl.DateTimeFormat(undefined, { day: "numeric", month: "short" }).format(date);
}

/** Parse a zone-less deadline as local wall clock, without any shifting. */
export function wallClockDate(value: string): Date {
  const [datePart, timePart = "00:00:00"] = value.split("T");
  const [y, m, d] = datePart!.split("-").map(Number);
  const [hh, mm] = timePart.split(":").map(Number);
  return new Date(y!, (m ?? 1) - 1, d ?? 1, hh ?? 0, mm ?? 0);
}

/** A deadline as written: "Fri 15 Aug, 17:00", or "Fri 15 Aug" when all-day. */
export function deadline(value: string | null | undefined, allDay = false): string {
  if (!value) return "No deadline";
  const date = wallClockDate(value);
  const day = new Intl.DateTimeFormat(undefined, { weekday: "short", day: "numeric", month: "short" }).format(date);
  if (allDay) return day;
  return `${day}, ${new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(date)}`;
}

/** "in 3 days" / "2 hours ago" for a deadline, measured in local wall clock. */
export function deadlineRelative(value: string | null | undefined, now = Date.now()): string {
  if (!value) return "";
  return relative(wallClockDate(value).toISOString(), now);
}

export function isOverdue(value: string | null | undefined, now = Date.now()): boolean {
  return !!value && wallClockDate(value).getTime() < now;
}

export function duration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export function minutes(value: number | null | undefined): string {
  if (value == null) return "—";
  if (value < 60) return `${Math.round(value)} min`;
  if (value < 60 * 24) return `${(value / 60).toFixed(value < 600 ? 1 : 0)} h`;
  return `${(value / 1440).toFixed(1)} d`;
}

export function number(value: number | null | undefined): string {
  return value == null ? "—" : new Intl.NumberFormat().format(value);
}

export function percent(value: number | null | undefined): string {
  return value == null ? "—" : `${value.toFixed(value % 1 === 0 ? 0 : 1)}%`;
}

export function initials(name: string | null | undefined, email?: string | null): string {
  const source = (name || email || "?").replace(/[^\p{L}\p{N}\s@._-]/gu, "").trim();
  const words = source.split(/[\s@._-]+/).filter(Boolean);
  return ((words[0]?.[0] ?? "?") + (words[1]?.[0] ?? "")).toUpperCase();
}

/** The browser's own time zone, for analytics bucketing and settings. */
export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}
