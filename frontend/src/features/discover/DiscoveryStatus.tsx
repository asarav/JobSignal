import { useEffect, useRef, useState } from "react";
import { useToast } from "../../components/Toast";
import { announceCountsChanged } from "../../hooks/useSetupStatus";
import {
  activeCrawlCount,
  discoveredCount,
  refreshDiscoveryStatus,
  useDiscoveryStatus,
} from "../../hooks/useDiscoveryStatus";
import { workerClient } from "../../services/workerClient";

function relative(iso?: string | null): string {
  if (!iso) return "not yet";
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return iso;
  const seconds = Math.round((at - Date.now()) / 1000);
  const abs = Math.abs(seconds);
  const units =
    abs < 60
      ? `${abs}s`
      : abs < 3600
        ? `${Math.round(abs / 60)}m`
        : abs < 86_400
          ? `${Math.round(abs / 3600)}h`
          : `${Math.round(abs / 86_400)}d`;
  return seconds >= 0 ? `in ${units}` : `${units} ago`;
}

function nextPass(iso?: string | null): string {
  if (!iso) return "not scheduled";
  const at = Date.parse(iso);
  if (Number.isNaN(at)) return iso;
  return at <= Date.now() ? "due now" : relative(iso);
}

function summaryLine(summary: Record<string, unknown> | null): string {
  if (!summary) return "";
  const value = (key: string) => Number(summary[key] ?? 0);
  return [
    `${value("registered")} new boards`,
    `${value("crawled")} boards crawled`,
    `${value("postings_harvested")} postings found`,
    `${value("failed")} failed`,
  ].join(" · ");
}

/**
 * What discovery is doing right now, and what its last pass did.
 *
 * The status itself comes from the shared discovery store, so this card and the
 * app-wide activity bar never disagree or poll twice.
 */
export function DiscoveryStatus({
  compact = false,
  onJobsChanged,
}: {
  compact?: boolean;
  onJobsChanged?: () => void;
}) {
  const toast = useToast();
  const value = useDiscoveryStatus();
  const status = value.status;
  const [manualBusy, setManualBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const revision = useRef(value.revision);

  useEffect(() => {
    if (value.revision === revision.current) return;
    revision.current = value.revision;
    onJobsChanged?.();
  }, [onJobsChanged, value.revision]);

  const runPass = async () => {
    setManualBusy(true);
    setError(null);
    try {
      const result = await workerClient.autoDiscover();
      toast.push(`Discovery pass finished: ${summaryLine(result as unknown as Record<string, unknown>)}.`, {
        tone: "success",
      });
      announceCountsChanged();
      await refreshDiscoveryStatus();
      onJobsChanged?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setManualBusy(false);
    }
  };

  const active = manualBusy || Boolean(status?.running) || activeCrawlCount(value) > 0;
  const total = status?.initial_total ?? 0;
  const done = Math.min(total, status?.initial_done ?? 0);
  const inFlight = activeCrawlCount(value);
  const summary = (value.lastResult ??
    (status?.last_summary as Record<string, unknown> | undefined) ??
    null) as Record<string, unknown> | null;
  const progress = total > 0 ? Math.round((done / total) * 100) : 0;

  const preparing = status?.phase === "seeding" && total === 0;
  const stateLine = active
    ? preparing
      ? `Preparing boards · ${status?.initial_pending ?? 0} waiting · ${discoveredCount(value)} postings found · ${inFlight} active.`
      : `Crawling ${done} of ${total || inFlight} boards · ${discoveredCount(value)} postings found · ${inFlight} active.`
    : status
      ? `${status.phase === "seeding" ? "Preparing boards" : "Background discovery runs"} every ${status.interval_minutes} min.` +
        ` Last pass ${relative(status.last_pass_at)}; next pass ${nextPass(status.next_pass_at)}.` +
        ` ${status.enabled_sources ?? 0} of ${status.sources ?? 0} boards enabled.`
      : "Checking discovery status…";

  if (compact) {
    return (
      <p className="text-xs text-slate-600" aria-live="polite" aria-busy={active}>
        <span className="font-medium text-slate-700">Discovery:</span> {stateLine}
        {summary ? ` Last pass: ${summaryLine(summary)}.` : ""}
      </p>
    );
  }

  return (
    <section
      aria-label="Discovery status"
      aria-busy={active}
      className="rounded-lg border border-line bg-white p-3 shadow-card"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-bold text-slate-900">Discovery</h2>
          <p className="mt-1 text-xs text-slate-600" aria-live="polite">
            {stateLine}
          </p>
          {active && total > 0 ? (
            <progress
              className="mt-2 h-2 w-full overflow-hidden rounded-full accent-brand-600"
              value={done}
              max={total}
            >
              {progress}% of boards crawled
            </progress>
          ) : null}
          {summary ? (
            <p className="mt-1 text-xs text-slate-600">
              Last pass: {summaryLine(summary)}.
            </p>
          ) : null}
          {status?.last_error ? (
            <p className="mt-1 text-xs text-rose-700">
              Last crawl error: {status.last_error}
            </p>
          ) : null}
          <p className="mt-1 text-[11px] text-slate-500">
            A pass walks every enabled board connector, crawls the boards that are due
            and searches public indexes for new boards. It is bounded per pass and
            repeats in the background, so more matches keep arriving over time.
          </p>
          {error ? <p className="mt-1 text-xs text-rose-700">{error}</p> : null}
        </div>
        <button
          type="button"
          onClick={() => void runPass()}
          disabled={manualBusy}
          className="rounded-md bg-brand-600 px-3 py-1.5 text-xs font-semibold text-white transition hover:bg-brand-700 disabled:opacity-50"
        >
          {manualBusy ? "Finding jobs…" : "Find new jobs everywhere"}
        </button>
      </div>
    </section>
  );
}
