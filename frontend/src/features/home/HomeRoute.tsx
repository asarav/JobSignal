import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Copy } from "../../copy/copy";
import { PageHeader } from "../../components/PageHeader";
import { RiskBadge } from "../../components/RiskBadge";
import { workerClient, WorkerError } from "../../services/workerClient";
import { useWorkerStore } from "../../stores/workerStore";
import type {
  ApplicationsListResponseApplicationInfo,
  CrawlRunsListResponseCrawlRunSummary,
  JobsRankedResponseJobSummary,
} from "../../types/generated";

const LAST_VISIT_KEY = "jobsignal.lastVisit";
const WIZARD_DISMISSED_KEY = "jobsignal.firstRunDismissed";

const FUNNEL_ORDER = ["applied", "acknowledged", "recruiter_contacted", "interviewing", "offered"];

function message(exc: unknown): string {
  return exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc);
}

export function HomeRoute() {
  const health = useWorkerStore((state) => state.health);
  const hello = useWorkerStore((state) => state.hello);
  const [jobs, setJobs] = useState<JobsRankedResponseJobSummary[]>([]);
  const [applications, setApplications] = useState<
    ApplicationsListResponseApplicationInfo[]
  >([]);
  const [runs, setRuns] = useState<CrawlRunsListResponseCrawlRunSummary[]>([]);
  // Headline counts come from the worker's whole-corpus aggregates; `null`
  // means "not reported yet", never 0.
  const [needsAttentionCount, setNeedsAttentionCount] = useState<number | null>(null);
  const [newSinceCount, setNewSinceCount] = useState<number | null>(null);
  const [hiddenCount, setHiddenCount] = useState<number | null>(null);
  const [rankWindow, setRankWindow] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hasResume, setHasResume] = useState(false);
  const [hasSources, setHasSources] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [wizardDismissed, setWizardDismissed] = useState(
    () => window.localStorage.getItem(WIZARD_DISMISSED_KEY) === "1",
  );
  const [lastVisit] = useState(() => {
    const stored = window.localStorage.getItem(LAST_VISIT_KEY);
    const parsed = stored ? Number(stored) : NaN;
    return Number.isNaN(parsed) ? 0 : parsed;
  });

  const load = useCallback(async () => {
    try {
      const [ranked, applied, crawlRuns, resume, sources] = await Promise.all([
        workerClient.rankedJobs({
          limit: 8,
          include_hidden: false,
          window: 500,
          seen_since: lastVisit ? new Date(lastVisit).toISOString() : undefined,
        }),
        workerClient.listApplications(),
        workerClient.listCrawlRuns(),
        workerClient.listResumeVersions(),
        workerClient.listSources(),
      ]);
      setJobs(ranked.jobs ?? []);
      // These are whole-corpus counts returned by the worker, not a filter of
      // the eight rows above. Undefined stays "not reported".
      setNeedsAttentionCount(ranked.needs_attention ?? null);
      setNewSinceCount(ranked.new_since ?? null);
      setHiddenCount(ranked.hidden_by_policy ?? null);
      setRankWindow(ranked.window ?? null);
      setApplications(applied.applications);
      setRuns(crawlRuns.runs);
      setHasResume((resume.versions ?? []).length > 0);
      setHasSources(sources.sources.length > 0);
      setError(null);
    } catch (exc) {
      setError(message(exc));
    } finally {
      setLoaded(true);
    }
  }, [lastVisit]);

  useEffect(() => {
    void load();
    window.localStorage.setItem(LAST_VISIT_KEY, String(Date.now()));
  }, [load]);

  const dismissWizard = () => {
    window.localStorage.setItem(WIZARD_DISMISSED_KEY, "1");
    setWizardDismissed(true);
  };

  const funnel = FUNNEL_ORDER.map((status) => ({
    status,
    count: applications.filter((row) => row.status === status).length,
  }));
  const lastRun = runs[0] ?? null;
  const showWizard = loaded && !wizardDismissed && (!hasResume || !hasSources);

  return (
    <div className="space-y-6">
      <PageHeader title={Copy.home.title} description={Copy.home.subtitle} />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      ) : null}

      {showWizard ? (
        <section className="rounded-lg border border-slate-300 bg-slate-50 p-4">
          <div className="flex flex-wrap items-start justify-between gap-2">
            <div>
              <h2 className="text-lg font-semibold text-slate-900">Set up in five steps</h2>
              <p className="mt-1 text-sm text-slate-600">
                Every step is optional and skippable. Nothing is uploaded; your resume and
                history stay on this computer.
              </p>
            </div>
            <button
              type="button"
              onClick={dismissWizard}
              className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-600 hover:bg-white"
            >
              Skip setup
            </button>
          </div>
          <ol className="mt-3 grid gap-2 text-sm text-slate-700 sm:grid-cols-2">
            <li>
              <span className="font-medium">1. Privacy.</span> Local-first: the worker
              stores everything in one encrypted-capable database on this machine.
            </li>
            <li>
              <span className="font-medium">2. Resume.</span>{" "}
              <Link className="underline" to="/resume">
                Import or paste a resume
              </Link>{" "}
              so fit can be scored. {hasResume ? "Done." : "Not done yet."}
            </li>
            <li>
              <span className="font-medium">3. Profile.</span>{" "}
              <Link className="underline" to="/search-profiles">
                Set hard filters and weights
              </Link>
              . Skipping keeps sensible defaults.
            </li>
            <li>
              <span className="font-medium">4. Sources.</span>{" "}
              <Link className="underline" to="/discover">
                Add a career page
              </Link>
              . {hasSources ? "Done." : "None added yet."}
            </li>
            <li>
              <span className="font-medium">5. First discovery.</span> Crawl from Discover;
              ranking and risk appear as postings arrive.
            </li>
          </ol>
        </section>
      ) : null}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-sm font-medium text-slate-500">New since your last visit</h2>
          <p className="mt-1 text-2xl font-semibold text-slate-900">
            {newSinceCount === null ? "—" : newSinceCount}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {lastVisit
              ? "All stored postings first seen after your last visit."
              : "Your first visit: every stored posting counts."}
          </p>
        </section>
        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-sm font-medium text-slate-500">Needs attention</h2>
          <p className="mt-1 text-2xl font-semibold text-slate-900">
            {needsAttentionCount === null ? "—" : needsAttentionCount}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {hiddenCount !== null && hiddenCount > 0
              ? `${hiddenCount} more hidden by your credibility policy across ${rankWindow === null ? "the ranking window" : `the ${rankWindow} most recent postings`}.`
              : "All stored postings that need verifying or look inactive."}
          </p>
        </section>
        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-sm font-medium text-slate-500">Applications in flight</h2>
          <p className="mt-1 text-2xl font-semibold text-slate-900">
            {applications.filter((row) =>
              ["applied", "acknowledged", "recruiter_contacted", "interviewing"].includes(
                row.status,
              ),
            ).length}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {applications.length} tracked in total. Nothing here is sent automatically.
          </p>
        </section>
        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-sm font-medium text-slate-500">Crawl status</h2>
          <p className="mt-1 text-2xl font-semibold text-slate-900">
            {lastRun ? lastRun.status : "—"}
          </p>
          <p className="mt-1 text-xs text-slate-500">
            {lastRun
              ? `Last run ${lastRun.started_at ?? "recently"}${lastRun.error ? ` · ${lastRun.error}` : ""}`
              : "No crawl has run yet."}
          </p>
        </section>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-slate-900">Top ranked</h2>
            <Link className="text-xs underline" to="/discover">
              Open Discover
            </Link>
          </div>
          {jobs.length === 0 ? (
            <p className="mt-2 text-sm text-slate-500">
              Nothing ranked yet. Add a source and crawl it, or compute fit for your profile.
            </p>
          ) : (
            <ul className="mt-3 divide-y divide-slate-100">
              {jobs.slice(0, 5).map((job) => (
                <li key={job.job_id} className="py-2">
                  <Link className="font-medium text-slate-900 underline" to={`/jobs/${job.job_id}`}>
                    {job.title}
                  </Link>
                  <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                    <span>{job.company_name || job.location_text || "—"}</span>
                    <span>
                      Fit {typeof job.fit_score === "number" ? job.fit_score.toFixed(2) : "—"}
                    </span>
                    <span>
                      Rank{" "}
                      {typeof job.rank_score === "number" ? job.rank_score.toFixed(2) : "—"}
                    </span>
                    <RiskBadge
                      riskClass={job.risk_class}
                      confidence={job.risk_confidence}
                      label={job.risk_label}
                    />
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-lg font-semibold text-slate-900">Application funnel</h2>
            <Link className="text-xs underline" to="/applications">
              Open Applications
            </Link>
          </div>
          <ul className="mt-3 space-y-1 text-sm text-slate-700">
            {funnel.map((step) => (
              <li key={step.status} className="flex items-center justify-between">
                <span className="capitalize">{step.status.replaceAll("_", " ")}</span>
                <span className="font-medium text-slate-900">{step.count}</span>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-slate-500">
            Counts come from your tracked applications, not from the employer.
          </p>
        </section>
      </div>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">{Copy.home.capabilities}</h2>
        <p className="mt-1 max-w-3xl text-sm text-slate-600">{Copy.home.welcome}</p>
        <p className="mt-2 text-xs text-slate-500">
          Worker health: <span>{health?.status ?? Copy.settings.none}</span> · schema{" "}
          <span>{health?.schema_version ?? Copy.settings.none}</span> · version{" "}
          <span>{hello?.app_version ?? health?.app_version ?? Copy.settings.none}</span>
        </p>
      </section>
    </div>
  );
}
