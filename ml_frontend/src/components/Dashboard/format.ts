/**
 * Dashboard timestamp as a short local date and time. The server sends "N/A" for an
 * experiment with no activity, so anything unparseable shows a dash.
 */
export function shortDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: date.getFullYear() === new Date().getFullYear() ? undefined : "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Milliseconds since the epoch, or 0 for a missing or unparseable value so it sorts last. */
export function toTimestamp(value: string | null | undefined): number {
  if (!value) return 0;
  const parsed = new Date(value).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
}
