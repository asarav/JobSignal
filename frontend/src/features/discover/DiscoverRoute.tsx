import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { InfoTip } from "../../components/InfoTip";
import { PageHeader } from "../../components/PageHeader";
import { RiskBadge } from "../../components/RiskBadge";
import { Copy } from "../../copy/copy";
import { workerClient, WorkerError } from "../../services/workerClient";
import type {
  JobsRankedResponseJobSummary,
  SourcesListResponseSourceInfo,
} from "../../types/generated";

const POLICIES: { value: string; label: string; hint: string }[] = [
  {
    value: "deprioritize",
    label: "Move low-credibility jobs down",
    hint: "Never hides a posting; a lower-credibility job ranks below stronger evidence.",
  },
  { value: "show_all", label: "Show everything", hint: "Fit order only; credibility is informational." },
  {
    value: "hide_high_risk",
    label: "Hide likely-inactive jobs",
    hint: "Only hides when risk is high and confidence is at least 0.5.",
  },
  {
    value: "require_evidence",
    label: "Require positive evidence",
    hint: "Only shows postings assessed as likely active.",
  },
];

const SORTS: { value: "rank" | "fit" | "freshness" | "credibility"; label: string }[] = [
  { value: "rank", label: "Best match" },
  { value: "fit", label: "Fit only" },
  { value: "freshness", label: "Newest" },
  { value: "credibility", label: "Credibility" },
];

export function DiscoverRoute() {
  const [jobs, setJobs] = useState<JobsRankedResponseJobSummary[]>([]);
  const [sources, setSources] = useState<SourcesListResponseSourceInfo[]>([]);
  const [url, setUrl] = useState("");
  const [query, setQuery] = useState("");
  const [policy, setPolicy] = useState("deprioritize");
  const [sort, setSort] = useState<"rank" | "fit" | "freshness" | "credibility">("rank");
  const [includeHidden, setIncludeHidden] = useState(false);
  const [includeSuppressed, setIncludeSuppressed] = useState(false);
  const [meta, setMeta] = useState<{
    total: number;
    hidden: number;
    window: number;
    truncated: boolean;
  } | null>(null);
  const [openReasons, setOpenReasons] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [lastRun, setLastRun] = useState<Record<string, unknown> | null>(null);
  const [lastAutoRun, setLastAutoRun] = useState<Record<string, unknown> | null>(null);

  const loadRanked = useCallback(
    async (ensureFit = false) => {
      const result = await workerClient.rankedJobs({
        query: query.trim() || undefined,
        ranking_policy: policy,
        sort,
        include_hidden: includeHidden,
        include_suppressed: includeSuppressed,
        ensure_fit: ensureFit,
        limit: 100,
      });
      setJobs(result.jobs ?? []);
      setMeta({
        total: result.total ?? 0,
        hidden: result.hidden_by_policy ?? 0,
        window: result.window ?? 0,
        truncated: Boolean(result.window_truncated),
      });
    },
    [query, policy, sort, includeHidden, includeSuppressed],
  );

  const load = useCallback(async () => {
    try {
      const sourceResult = await workerClient.listSources();
      setSources(sourceResult.sources);
      await loadRanked();
      setError(null);
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    }
  }, [loadRanked]);

  useEffect(() => {
    void load();
  }, [load]);

  const guard = async (work: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await work();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const addSource = () =>
    guard(async () => {
      if (!url.trim()) return;
      await workerClient.addSource({ url: url.trim(), interval_s: 3600 });
      setUrl("");
      await load();
    });

  const runDiscovery = (sourceId: string) =>
    guard(async () => {
      setLastRun(null);
      const result = await workerClient.runDiscovery({ source_id: sourceId });
      setLastRun(result as unknown as Record<string, unknown>);
      await load();
    });

  const autoDiscover = () =>
    guard(async () => {
      setLastAutoRun(null);
      const result = await workerClient.autoDiscover();
      setLastAutoRun(result as unknown as Record<string, unknown>);
      await load();
    });

  const toggleSaved = (jobId: string, saved: boolean) =>
    guard(async () => {
      await workerClient.setJobUserState({ job_id: jobId, state: saved ? "new" : "saved" });
      await loadRanked();
    });

  return (
    <div className="space-y-6">
      <PageHeader
        title="Find jobs"
        description="Your shortlist: hard filters first, then a score built from fit and credibility. Every order can explain itself."
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
            {" "}{String(lastAutoRun.crawled ?? 0)} crawled,
            {" "}{String(lastAutoRun.failed ?? 0)} failed.
          </p>
        ) : null}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold text-slate-900" aria-label={`Jobs (${jobs.length})`}>
            Jobs ({meta ? meta.total : `${jobs.length} shown`})
            <InfoTip label="Jobs">{Copy.tooltips.results}</InfoTip>
          </h2>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <input
              className="rounded border border-slate-300 px-2 py-1"
              placeholder="Filter words"
              aria-label="Filter words"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
            <select
              className="rounded border border-slate-300 px-2 py-1"
              aria-label="Ranking policy"
              value={policy}
              onChange={(event) => setPolicy(event.target.value)}
              title={POLICIES.find((entry) => entry.value === policy)?.hint}
            >
              {POLICIES.map((entry) => (
                <option key={entry.value} value={entry.value}>
                  {entry.label}
                </option>
              ))}
            </select>
            <select
              className="rounded border border-slate-300 px-2 py-1"
              aria-label="Sort order"
              value={sort}
              onChange={(event) => setSort(event.target.value as typeof sort)}
            >
              {SORTS.map((entry) => (
                <option key={entry.value} value={entry.value}>
                  {entry.label}
                </option>
              ))}
            </select>
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={includeHidden}
                onChange={(event) => setIncludeHidden(event.target.checked)}
              />
              Show hidden
            </label>
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={includeSuppressed}
                onChange={(event) => setIncludeSuppressed(event.target.checked)}
              />
              Show suppressed
            </label>
            <button
              type="button"
              disabled={busy}
              onClick={() => void guard(async () => loadRanked(true))}
              className="rounded border border-slate-300 px-2 py-1 font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              title="Compute fit for the current ranking window (bounded)."
            >
              Compute fit
            </button>
          </div>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          {meta && meta.window !== undefined && meta.window !== null
            ? `Ranked over the ${meta.window} most recent matching postings`
            : "Ranked over the most recent matching postings"}
          {meta?.truncated ? " (the window was full; older postings are reachable by search)" : ""}.
          {meta && meta.hidden > 0 ? ` ${meta.hidden} hidden by the selected policy.` : ""}
        </p>

        {jobs.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            No jobs yet. Add a source above and crawl it, or run a discovery pass.
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-100">
            {jobs.map((job) => {
              const reasonsOpen = openReasons === job.job_id;
              return (
                <li key={job.job_id} className="py-2">
                  <div className="flex flex-wrap items-start gap-2">
                    <div className="min-w-0 flex-1">
                      <Link
                        className="font-medium text-slate-900 underline"
                        to={`/jobs/${job.job_id}`}
                      >
                        {job.title}
                      </Link>
                      <div className="text-xs text-slate-500">
                        {[job.company_name, job.location_text, job.workplace_type]
                          .filter((value) => value && value !== "unknown")
                          .join(" · ")}
                      </div>
                      <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                        <span title="Satisfaction: how well the posting matches your profile.">
                          Fit{" "}
                          {typeof job.fit_score === "number"
                            ? job.fit_score.toFixed(2)
                            : "—"}
                        </span>
                        <span title="Success evidence: 1 − risk, shown separately from confidence.">
                          Credibility{" "}
                          {typeof job.credibility === "number"
                            ? job.credibility.toFixed(2)
                            : "—"}
                        </span>
                        <span title="Rank score combines fit with credibility under your policy.">
                          Rank{" "}
                          {typeof job.rank_score === "number"
                            ? job.rank_score.toFixed(2)
                            : "—"}
                        </span>
                        <RiskBadge
                          riskClass={job.hidden_by_policy ? null : job.risk_class}
                          confidence={job.risk_confidence}
                          label={job.risk_label}
                        />
                        {job.hidden_by_policy ? (
                          <span className="rounded-full bg-slate-200 px-2 py-0.5 text-slate-700">
                            Hidden by policy
                          </span>
                        ) : null}
                      </div>
                    </div>
                    <div className="flex shrink-0 gap-2">
                      <button
                        type="button"
                        onClick={() => setOpenReasons(reasonsOpen ? null : job.job_id)}
                        aria-expanded={reasonsOpen}
                        className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
                      >
                        Why this rank
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void toggleSaved(job.job_id, Boolean(job.saved))}
                        className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      >
                        {job.saved ? "Saved ✓" : "Save"}
                      </button>
                    </div>
                  </div>
                  {reasonsOpen ? (
                    <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-slate-600" aria-label="Why this rank">
                      {(job.rank_reasons ?? []).map((reason, index) => (
                        <li key={index}>{reason}</li>
                      ))}
                      {(job.rank_reasons ?? []).length === 0 ? (
                        <li>No ranking evidence recorded yet.</li>
                      ) : null}
                    </ul>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}
