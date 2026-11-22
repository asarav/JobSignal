import { useCallback, useEffect, useState } from "react";
import { InfoTip } from "../../components/InfoTip";
import { PageHeader } from "../../components/PageHeader";
import { Copy } from "../../copy/copy";
import { workerClient, WorkerError } from "../../services/workerClient";
import type { JobsListResponseJobSummary, SourcesListResponseSourceInfo } from "../../types/generated";

export function DiscoverRoute() {
  const [jobs, setJobs] = useState<JobsListResponseJobSummary[]>([]);
  const [sources, setSources] = useState<SourcesListResponseSourceInfo[]>([]);
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lastRun, setLastRun] = useState<Record<string, unknown> | null>(null);
  const [lastAutoRun, setLastAutoRun] = useState<Record<string, unknown> | null>(null);

  const load = useCallback(async () => {
    try {
      const [jobResult, sourceResult] = await Promise.all([
        workerClient.listJobs({ limit: 50 }),
        workerClient.listSources(),
      ]);
      setJobs(jobResult.jobs);
      setSources(sourceResult.sources);
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const addSource = async () => {
    if (!url.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await workerClient.addSource({ url: url.trim(), interval_s: 3600 });
      setUrl("");
      await load();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const runDiscovery = async (sourceId: string) => {
    setBusy(true);
    setError(null);
    setLastRun(null);
    try {
      const result = await workerClient.runDiscovery({ source_id: sourceId });
      setLastRun(result as Record<string, unknown>);
      await load();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const autoDiscover = async () => {
    setBusy(true);
    setError(null);
    setLastAutoRun(null);
    try {
      const result = await workerClient.autoDiscover();
      setLastAutoRun(result as Record<string, unknown>);
      await load();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Discover jobs"
        description="Add a career page or job board URL, crawl it, and review the postings found."
      />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900" aria-label="Sources">
          Sources
          <InfoTip label="Sources">{Copy.tooltips.sources}</InfoTip>
        </h2>
        <div className="mt-3 flex gap-2">
          <input
            className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm"
            placeholder="Paste a career page URL, e.g. https://boards.greenhouse.io/your-company"
            aria-label="Career page or job board URL"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <button
            type="button"
            onClick={() => void addSource()}
            disabled={busy}
            className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {busy ? "Adding…" : "Add source"}
          </button>
        </div>
        <ul className="mt-3 space-y-2">
          {sources.map((source) => (
            <li key={source.source_id} className="flex items-center justify-between rounded border border-slate-200 px-3 py-2 text-sm">
              <span className="flex min-w-0 flex-col gap-0.5">
                <span className="flex min-w-0 items-center gap-2">
                  <span className="truncate">{source.url}</span>
                  {source.tier ? (
                    <span
                      title={`${Copy.tooltips.tier}${
                        source.tier_reason ? ` Reason: ${source.tier_reason}` : ""
                      }`}
                      aria-label={
                        source.tier_reason
                          ? `Discovery tier ${source.tier}: ${source.tier_reason}`
                          : `Discovery tier ${source.tier}`
                      }
                      className={
                        source.tier === "hot"
                          ? "shrink-0 rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800"
                          : source.tier === "warm"
                            ? "shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800"
                            : "shrink-0 rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600"
                      }
                    >
                      {source.tier}
                    </span>
                  ) : null}
                </span>
                {source.tier && source.tier_reason ? (
                  <span className="text-xs text-slate-500">{source.tier_reason}</span>
                ) : null}
              </span>
              <button
                type="button"
                onClick={() => void runDiscovery(source.source_id)}
                title={Copy.tooltips.runSource}
                disabled={busy}
                className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                {busy ? "Crawling…" : "Crawl now"}
              </button>
            </li>
          ))}
          {sources.length === 0 ? (
            <li className="text-sm text-slate-500">
              No sources yet. Add a career page or job board URL above.
            </li>
          ) : null}
        </ul>
      </section>

      {lastRun ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
          Last crawl: {String(lastRun.discovered ?? 0)} postings found,
          {" "}{String(lastRun.new ?? 0)} new, {String(lastRun.updated ?? 0)} updated,
          {" "}{String(lastRun.skipped ?? 0)} unchanged, {String(lastRun.removed ?? 0)} removed,
          {" "}{String(lastRun.errors ?? 0)} errors.
        </div>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900" aria-label="Auto-discovery">
          Auto-discovery
          <InfoTip label="Auto-discovery">{Copy.tooltips.autoDiscovery}</InfoTip>
        </h2>
        <p className="mt-1 text-sm text-slate-500">
          The worker finds new career sites, registers them and crawls sources whose
          crawl interval is due.
        </p>
        <p className="mt-1 text-xs text-slate-400">
          Runs continuously in the background, one bounded pass at a time, so you
          do not need to add sources yourself. Boards and their postings are
          re-checked as their interval comes due; coverage grows with every pass.
        </p>
        <button
          type="button"
          onClick={() => void autoDiscover()}
          disabled={busy}
          className="mt-3 rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {busy ? "Running pass…" : "Run discovery pass"}
        </button>
        {lastAutoRun ? (
          <p className="mt-3 text-sm text-slate-600">
            {String(lastAutoRun.discovered ?? 0)} sites found,
            {" "}{String(lastAutoRun.registered ?? 0)} new sources,
            {" "}{String(lastAutoRun.postings_verified ?? 0)} indexed postings verified,
            {" "}{String(lastAutoRun.tiers_changed ?? 0)} tier changes,
            {" "}{String(lastAutoRun.crawled ?? 0)} crawled,
            {" "}{String(lastAutoRun.failed ?? 0)} failed.
          </p>
        ) : null}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2
          className="text-lg font-semibold text-slate-900"
          aria-label={`Jobs (${jobs.length})`}
        >
          Jobs ({jobs.length})
          <InfoTip label="Jobs">{Copy.tooltips.results}</InfoTip>
        </h2>
        {jobs.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            No jobs yet. Add a source above and crawl it, or run a discovery pass.
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-100">
            {jobs.map((job) => (
              <li key={job.job_id} className="py-2">
                <div className="font-medium text-slate-900">{job.title}</div>
                <div className="flex items-center gap-2 text-xs text-slate-500">
                  <span className="truncate">{job.canonical_url}</span>
                  {job.via_index ? (
                    <span
                      title={Copy.tooltips.viaIndex}
                      className="shrink-0 rounded-full bg-sky-100 px-2 py-0.5 text-xs font-medium text-sky-800"
                    >
                      via search index
                    </span>
                  ) : null}
                </div>
                {job.provenance && job.provenance.length > 0 ? (
                  <div className="mt-1 text-xs text-slate-400">
                    <span title={Copy.tooltips.extractedBy}>
                      Extracted by: {job.provenance.join(" → ")}
                    </span>
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
