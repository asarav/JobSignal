import { Link } from "react-router-dom";
import {
  activeCrawlCount,
  discoveredCount,
  useDiscoveryStatus,
  type DiscoverySnapshot,
} from "../../hooks/useDiscoveryStatus";

function activityLine(value: DiscoverySnapshot): string {
  const status = value.status;
  const active = activeCrawlCount(value);
  const liveProcessed = Object.values(value.live).reduce(
    (sum, run) => sum + run.processed,
    0,
  );
  const liveTotal = Object.values(value.live).reduce(
    (sum, run) => sum + run.refsTotal,
    0,
  );
  if (status?.running || active > 0) {
    const done = Math.min(status?.initial_total ?? 0, status?.initial_done ?? 0);
    const total = Math.max(status?.initial_total ?? 0, done);
    const suffix = liveTotal > 0 ? ` · ${liveProcessed}/${liveTotal} postings this board` : "";
    if (total === 0 && status?.phase === "seeding") {
      return `Preparing boards · ${discoveredCount(value)} postings found · ${active} active${suffix}`;
    }
    const boards =
      total > 0
        ? `Crawling ${done} of ${total} boards`
        : `Crawling ${active} active board${active === 1 ? "" : "s"}`;
    return `${boards} · ${discoveredCount(value)} postings found${total > 0 ? ` · ${active} active` : ""}${suffix}`;
  }
  if (!status) return "Discovery · checking status…";
  const enabled = status?.enabled_sources ?? 0;
  return `Discovery idle · ${enabled} board${enabled === 1 ? "" : "s"} · ${discoveredCount(value)} postings found`;
}

/** Always-visible answer to "is it crawling?", on every screen. */
export function DiscoveryActivity({ tone = "light" }: { tone?: "light" | "dark" }) {
  const value = useDiscoveryStatus();
  const status = value.status;
  const total = status?.initial_total ?? 0;
  const done = Math.min(total, status?.initial_done ?? 0);
  const active = Boolean(status?.running) || activeCrawlCount(value) > 0;
  const dark = tone === "dark";

  return (
    <section
      aria-label="Discovery activity"
      aria-live="polite"
      aria-busy={active}
      className={
        dark
          ? "border-t border-white/10 px-3 py-3 text-[11px] text-slate-300"
          : "rounded-lg border border-line bg-white px-3 py-2 text-xs text-slate-600 shadow-card"
      }
    >
      <div className="flex items-center gap-2">
        {active ? (
          <span
            className="inline-block h-3 w-3 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent"
            aria-hidden="true"
          />
        ) : null}
        <p className="min-w-0 flex-1 leading-snug">{activityLine(value)}</p>
        <Link
          to="/discover"
          className={
            dark
              ? "shrink-0 font-semibold text-accent-300 underline"
              : "shrink-0 font-semibold text-brand-700 underline"
          }
        >
          Find jobs
        </Link>
      </div>
      {active && total > 0 ? (
        <progress
          className="mt-2 h-1.5 w-full overflow-hidden rounded-full accent-brand-600"
          value={done}
          max={total}
        >
          {done} of {total} boards
        </progress>
      ) : null}
      {value.error ? (
        <p className={dark ? "mt-1 text-rose-300" : "mt-1 text-rose-700"}>
          Status unavailable: {value.error}
        </p>
      ) : null}
    </section>
  );
}
