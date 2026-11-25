export const LAST_VISIT_KEY = "jobsignal.lastVisit";
export const LAST_VISIT_FALLBACK_DAYS = 7;

/** The previous visit's timestamp, or null on a first run. */
export function readLastVisit(): number | null {
  const stored = window.localStorage.getItem(LAST_VISIT_KEY);
  if (!stored) return null;
  const parsed = Number(stored);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

/** ISO cutoff for "new since last visit"; first run falls back to one week. */
export function lastVisitCutoff(now = Date.now(), visit = readLastVisit()): string {
  const cutoff = visit ?? now - LAST_VISIT_FALLBACK_DAYS * 86_400_000;
  return new Date(cutoff).toISOString().replace(/\.\d{3}Z$/, "Z");
}

/**
 * Record the new baseline when the current visit ends. It is deliberately not
 * written on mount: Today's "Review new matches" link must still see the
 * previous baseline while the user is reviewing this session's arrivals.
 */
export function recordVisitOnExit(): () => void {
  const write = () => {
    window.localStorage.setItem(LAST_VISIT_KEY, String(Date.now()));
  };
  const onVisibility = () => {
    if (document.visibilityState === "hidden") write();
  };
  window.addEventListener("beforeunload", write);
  document.addEventListener("visibilitychange", onVisibility);
  return () => {
    window.removeEventListener("beforeunload", write);
    document.removeEventListener("visibilitychange", onVisibility);
  };
}
