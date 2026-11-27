import { useCallback, useEffect, useRef, useState } from "react";
import { useToast } from "../../components/Toast";
import { announceCountsChanged } from "../../hooks/useSetupStatus";
import { workerClient } from "../../services/workerClient";
import type { DiscoveryGetStatusResponse, Envelope } from "../../types/generated";

type LastSummary = {
  discovered?: number;
  registered?: number;
  crawled?: number;
  failed?: number;
  postings_harvested?: number;
  postings_verified?: number;
};

type LiveRuns = Record<string, { sourceId: string; refs: number }>;

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
  // A stored next time can be behind the clock when the worker was stopped.
  return at <= Date.now() ? "due now" : relative(iso);
}

function summaryLine(summary: LastSummary | null): string {
  if (!summary) return "";
  return [
    `${summary.registered ?? 0} new boards`,
    `${summary.crawled ?? 0} boards crawled`,
    `${summary.postings_harvested ?? 0} postings found`,
    `${summary.failed ?? 0} failed`,
  ].join(" · ");
}

/**
 * What discovery is doing right now, and what its last pass did.
 *
 * The worker already runs bounded passes in the background; without this the
 * app looked idle while postings trickled in, and a manual crawl looked like it
 * only ever touched one board.
 */
export function DiscoveryStatus({
  compact = false,
  onJobsChanged,
}: {
  compact?: boolean;
  onJobsChanged?: () => void;
}) {
  const toast = useToast();
  // The toast identity can change with the provider, and the event subscription
  // must not be rebuilt for it; the refs keep the callback current.
  const toastRef = useRef(toast);
  toastRef.current = toast;
  const [status, setStatus] = useState<DiscoveryGetStatusResponse | null>(null);
  const [manualBusy, setManualBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [live, setLive] = useState<LiveRuns>({});
  const [finishedBoards, setFinishedBoards] = useState(0);
  const [finishedPostings, setFinishedPostings] = useState(0);
  const [lastResult, setLastResult] = useState<LastSummary | null>(null);
  const manualBusyRef = useRef(false);

  useEffect(() => {
    manualBusyRef.current = manualBusy;
  }, [manualBusy]);

  const loadStatus = useCallback(async () => {
    try {
      setStatus(await workerClient.getDiscoveryStatus());
    } catch {
      // A status read is informational; a failure must not break the screen.
      setStatus(null);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    let cancelled = false;
    let unlisten: (() => void) | null = null;
    const subscribe = workerClient.onEvent;
    if (typeof subscribe !== "function") return;
    const handle = (event: Envelope) => {
      const data = (event.data ?? {}) as Record<string, unknown>;
      if (event.topic === "crawl.started") {
        const runId = String(data.run_id ?? "");
        if (!runId) return;
        setLive((current) => ({
          ...current,
          [runId]: { sourceId: String(data.source_id ?? ""), refs: 0 },
        }));
      } else if (event.topic === "discovery.progress") {
        const runId = String(data.run_id ?? "");
        const refs = Number(data.refs_discovered ?? 0);
        if (!runId) return;
        setLive((current) => ({
          ...current,
          [runId]: {
            sourceId: current[runId]?.sourceId ?? String(data.source_id ?? ""),
            refs: Number.isFinite(refs) ? refs : 0,
          },
        }));
      } else if (event.topic === "crawl.completed" || event.topic === "crawl.failed") {
        const runId = String(data.run_id ?? "");
        setLive((current) => {
          const next = { ...current };
          delete next[runId];
          return next;
        });
        if (event.topic === "crawl.completed") {
          const stats = (data.stats ?? {}) as Record<string, unknown>;
          const discovered = Number(stats.discovered ?? 0);
          setFinishedBoards((value) => value + 1);
          setFinishedPostings((value) =>
            value + (Number.isFinite(discovered) ? discovered : 0),
          );
        }
        void loadStatus();
        onJobsChanged?.();
      } else if (event.topic === "discovery.pass_started") {
        setFinishedBoards(0);
        setFinishedPostings(0);
      } else if (event.topic === "discovery.pass_completed") {
        const summary = data as LastSummary;
        setLastResult(summary);
        setFinishedBoards(0);
        setFinishedPostings(0);
        void loadStatus();
        onJobsChanged?.();
        // A manual pass already toasts from its own response; a background
        // pass reports only when it actually added something worth knowing.
        const added =
          Number(summary.registered ?? 0) + Number(summary.postings_harvested ?? 0);
        if (!manualBusyRef.current && added > 0) {
          toastRef.current.push(
            `Discovery pass finished: ${summaryLine(summary)}.`,
            { tone: "success" },
          );
        }
      }
    };
    try {
      void subscribe(handle)
        .then((stop) => {
          if (cancelled) stop();
          else unlisten = stop;
        })
        .catch(() => undefined);
    } catch {
      // A bridge without event support still shows the status and pass button.
      return;
    }
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [loadStatus, onJobsChanged]);

  const runPass = async () => {
    setManualBusy(true);
    setError(null);
    setFinishedBoards(0);
    setFinishedPostings(0);
    try {
      const result = await workerClient.autoDiscover();
      setLastResult(result as LastSummary);
      const line = summaryLine(result as LastSummary);
      toast.push(`Discovery pass finished: ${line}.`, { tone: "success" });
      announceCountsChanged();
      await loadStatus();
      onJobsChanged?.();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setManualBusy(false);
    }
  };

  const inFlight = Object.keys(live).length;
  const seenPostings =
    Object.values(live).reduce((sum, run) => sum + run.refs, 0) + finishedPostings;
  const boardsThisRun = finishedBoards;
  const active = manualBusy || Boolean(status?.running) || inFlight > 0;
  const summary = lastResult ?? (status?.last_summary as LastSummary | undefined) ?? null;

  const stateLine = active
    ? `Collecting now: ${inFlight} board${inFlight === 1 ? "" : "s"} in progress,` +
      ` ${seenPostings} posting${seenPostings === 1 ? "" : "s"} seen` +
      (boardsThisRun > 0 ? `, ${boardsThisRun} finished.` : ".")
    : status
      ? `Background discovery runs every ${status.interval_minutes} min.` +
        ` Last pass ${relative(status.last_pass_at)}; next pass ${nextPass(status.next_pass_at)}.` +
        ` ${status.enabled_sources ?? 0} of ${status.sources ?? 0} boards enabled.`
      : "Checking discovery status…";

  if (compact) {
    return (
      <p className="text-xs text-slate-600" aria-live="polite">
        <span className="font-medium text-slate-700">Discovery:</span> {stateLine}
        {summary ? ` Last pass: ${summaryLine(summary)}.` : ""}
      </p>
    );
  }

  return (
    <section
      aria-label="Discovery status"
      className="rounded-lg border border-line bg-white p-3 shadow-card"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-bold text-slate-900">Discovery</h2>
          <p className="mt-1 text-xs text-slate-600" aria-live="polite">
            {stateLine}
          </p>
          {summary ? (
            <p className="mt-1 text-xs text-slate-600">
              Last pass: {summaryLine(summary)}.
            </p>
          ) : null}
          <p className="mt-1 text-[11px] text-slate-500">
            A pass walks every enabled board connector, crawls the boards that are due and
            searches public indexes for new boards. It is bounded per pass and repeats in
            the background, so more matches keep arriving over time.
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
