/**
 * Turning stored timestamps into API strings.
 *
 * The schema stores naive timestamps with one convention: they are UTC — except
 * `commitments.deadline`, which is the wall-clock time the email named. So
 * there are exactly two conversions, and choosing the wrong one would shift a
 * "5pm" deadline by the user's UTC offset.
 */

/** A stored UTC instant -> ISO-8601 with `Z`. */
export function utc(value: string | null | undefined): string | null {
  if (!value) return null;
  const iso = value.includes("T") ? value : value.replace(" ", "T");
  return iso.endsWith("Z") ? iso : `${iso}Z`;
}

/** A stored wall-clock time -> ISO-8601 *without* a zone, shown as written. */
export function wallClock(value: string | null | undefined): string | null {
  if (!value) return null;
  return value.includes("T") ? value : value.replace(" ", "T");
}

/** Whether a wall-clock deadline is midnight, which the app treats as all-day. */
export function isAllDay(value: string | null | undefined): boolean {
  return !!value && /[ T]00:00(:00(\.0+)?)?$/.test(value);
}

/** Now, as a naive-UTC string for parameters compared against stored values. */
export function nowUtcText(date = new Date()): string {
  return date.toISOString().replace("T", " ").replace("Z", "");
}
