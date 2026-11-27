import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Copy } from "../../copy/copy";
import { Button, buttonClass } from "../../components/Button";
import { CompanyAvatar } from "../../components/CompanyAvatar";
import { EmptyState } from "../../components/EmptyState";
import { Metric } from "../../components/Metric";
import { PageHeader } from "../../components/PageHeader";
import { RiskBadge } from "../../components/RiskBadge";
import { ScoreMeter } from "../../components/ScoreMeter";
import { SearchPlan } from "../../components/SearchPlan";
import { nextStep, type WorkflowState } from "../workflow/nextStep";
import { lastVisitCutoff, readLastVisit } from "../workflow/lastVisit";
import { CollectJobsAction } from "../workflow/CollectJobsAction";
import { DiscoveryStatus } from "../discover/DiscoveryStatus";
import { workerClient, WorkerError } from "../../services/workerClient";
import { useLiveRefresh } from "../../hooks/useLiveRefresh";
import type {
  ApplicationsListResponseApplicationSummary,
  JobsRankedResponseJobSummary,
} from "../../types/generated";

const EMPTY_SUMMARY: ApplicationsListResponseApplicationSummary = {
  total: 0,
  applied: 0,
  active: 0,
  interviewing: 0,
  offers: 0,
  closed: 0,
  follow_up: 0,
  applied_last_7_days: 0,
  responded_last_7_days: 0,
};

function message(exc: unknown): string {
  return exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc);
}

function ageLabel(value?: string | null): string {
  if (!value) return "date not recorded";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "date not recorded";
  const days = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 86_400_000));
  if (days === 0) return "new today";
  if (days === 1) return "1 day old";
  return `${days} days old`;
}

function FocusCard({
  tone,
  eyebrow,
  count,
  title,
  body,
  cta,
  to,
  empty,
  emptyBody,
}: {
  tone: "brand" | "amber" | "rose";
  eyebrow: string;
  count: number;
  title: string;
  body: string;
  cta: string;
  to: string;
  empty: string;
  emptyBody: string;
}) {
  const tones = {
    brand: "border-brand-200 bg-brand-50/70 text-brand-800",
    amber: "border-amber-200 bg-amber-50/70 text-amber-900",
    rose: "border-rose-200 bg-rose-50/70 text-rose-900",
  } as const;
  const bar = {
    brand: "bg-brand-500",
    amber: "bg-amber-500",
    rose: "bg-rose-500",
  } as const;
  const active = count > 0;
  return (
    <div className={`relative overflow-hidden rounded-lg border p-4 ${active ? tones[tone] : "border-line bg-white text-slate-500"}`}>
      <span className={`absolute inset-y-0 left-0 w-1 ${active ? bar[tone] : "bg-slate-200"}`} aria-hidden="true" />
      <p className="text-[11px] font-semibold uppercase tracking-widest opacity-70">{eyebrow}</p>
      <p className="tabular mt-1 text-3xl font-black tracking-tight">{count}</p>
      <p className="mt-1 text-sm font-semibold">{active ? title : empty}</p>
      <p className="mt-1 min-h-[2.5rem] text-xs opacity-80">{active ? body : emptyBody}</p>
      <Link
        to={to}
        className={buttonClass(active ? "primary" : "secondary", "mt-3 w-full justify-center")}
      >
        {cta}
      </Link>
    </div>
  );
}

export function HomeRoute() {
  const [jobs, setJobs] = useState<JobsRankedResponseJobSummary[]>([]);
  const [totalMatches, setTotalMatches] = useState(0);
  const [newSinceCount, setNewSinceCount] = useState(0);
  const [needsAttentionCount, setNeedsAttentionCount] = useState(0);
  const [hiddenCount, setHiddenCount] = useState(0);
  const [rankWindow, setRankWindow] = useState<number | null>(null);
  const [summary, setSummary] =
    useState<ApplicationsListResponseApplicationSummary>(EMPTY_SUMMARY);
  const [readyToApply, setReadyToApply] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [hasResume, setHasResume] = useState(false);
  const [hasSources, setHasSources] = useState(false);
  const [lastCrawl, setLastCrawl] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [lastVisit] = useState(() => readLastVisit());

  const load = useCallback(async () => {
    try {
      const [ranked, applied, saved, resume, sources, crawlRuns] = await Promise.all([
        workerClient.rankedJobs({
          limit: 3,
          include_hidden: false,
          ensure_fit: true,
          window: 500,
          seen_since: lastVisitCutoff(Date.now(), lastVisit),
        }),
        workerClient.listApplications({ limit: 1 }),
        workerClient.listSavedJobs({ view: "ready", limit: 1 }),
        workerClient.listResumeVersions(),
        workerClient.listSources(),
        workerClient.listCrawlRuns({ limit: 1 }),
      ]);
      setJobs(ranked.jobs ?? []);
      setTotalMatches(ranked.total ?? ranked.jobs?.length ?? 0);
      setNewSinceCount(ranked.new_since ?? 0);
      setNeedsAttentionCount(ranked.needs_attention ?? 0);
      setHiddenCount(ranked.hidden_by_policy ?? 0);
      setRankWindow(ranked.window ?? null);
      setSummary(applied.summary ?? EMPTY_SUMMARY);
      setReadyToApply(saved.total ?? saved.jobs.length);
      setHasResume((resume.versions ?? []).length > 0);
      setHasSources(sources.sources.length > 0);
      const latestRun = crawlRuns.runs[0] ?? null;
      setLastCrawl(latestRun?.finished_at ?? latestRun?.started_at ?? null);
      setError(null);
    } catch (exc) {
      setError(message(exc));
    } finally {
      setLoaded(true);
    }
  }, [lastVisit]);

  useEffect(() => {
    void load();
  }, [load]);
  useLiveRefresh(load);

  const workflowState: WorkflowState = {
    hasResume,
    hasSources,
    hasMatches: totalMatches > 0,
    matchCount: totalMatches,
    savedCount: readyToApply,
    appliedCount: summary.total ?? 0,
    lastCrawl,
    newSince: newSinceCount,
    readyToApply,
    followUps: summary.follow_up ?? 0,
    appliedLast7Days: summary.applied_last_7_days ?? 0,
    responsesLast7Days: summary.responded_last_7_days ?? 0,
  };
  const step = nextStep(workflowState);
  const established =
    hasResume && hasSources && (totalMatches > 0 || (summary.total ?? 0) > 0 || readyToApply > 0);
  const queueClear = newSinceCount === 0 && readyToApply === 0 && (summary.follow_up ?? 0) === 0;

  const stateLine = (() => {
    if (!hasSources) {
      return "No sources yet. Add a company careers page or job board to start collecting postings.";
    }
    if (!hasResume) {
      return totalMatches > 0
        ? `${totalMatches} matches collected. Import a resume to rank them against your experience.`
        : "No matches yet. Import a resume so fit scores can rank the postings you collect.";
    }
    if (newSinceCount === 0) {
      return lastVisit === null
        ? "No new postings in the last seven days."
        : "No new postings since your last visit.";
    }
    if (lastVisit === null) {
      return `${newSinceCount} new in the last seven days.`;
    }
    return `${newSinceCount} new since your last visit.`;
  })();

  const reviewTo = newSinceCount > 0 ? "/discover?new=1" : "/discover";

  return (
    <div className="space-y-6">
      <div>
        <PageHeader
          title={Copy.home.title}
          description={
            established
              ? "A short queue for today, then you are done."
              : Copy.home.subtitle
          }
          actions={
            established ? (
              <Button variant="secondary" onClick={() => void load()}>
                Refresh
              </Button>
            ) : undefined
          }
        />
        <p className="mt-2 text-sm text-slate-600">{stateLine}</p>
        <div className="mt-1">
          <DiscoveryStatus compact onJobsChanged={load} />
        </div>
      </div>

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      ) : null}

      {!loaded ? (
        <p className="text-sm text-slate-500">Loading your plan…</p>
      ) : (
        <>
          {established ? (
            <>
              <section aria-label="Today’s focus">
                <div className="flex flex-wrap items-end justify-between gap-2">
                  <div>
                    <h2 className="text-lg font-bold tracking-tight text-slate-900">Today’s focus</h2>
                    <p className="text-sm text-slate-500">
                      Three queues, ranked by what moves your search forward.
                    </p>
                  </div>
                  {needsAttentionCount > 0 ? (
                    <Link to="/discover?view=search" className="text-xs font-medium text-sky-700 underline">
                      {needsAttentionCount} postings need verification
                    </Link>
                  ) : null}
                </div>
                <div className="mt-3 grid gap-3 md:grid-cols-3">
                  <FocusCard
                    tone="brand"
                    eyebrow="Review"
                    count={newSinceCount}
                    title={
                      lastVisit === null
                        ? "new matches in the last seven days"
                        : "new matches since your last visit"
                    }
                    body={`${totalMatches} total in the ranked window. Triage the new ones first.`}
                    cta="Review new matches"
                    to={reviewTo}
                    empty="New matches"
                    emptyBody="No new postings. Review all matches or add another source."
                  />
                  <FocusCard
                    tone="amber"
                    eyebrow="Apply"
                    count={readyToApply}
                    title="ready on your shortlist"
                    body="Open one, apply yourself, then mark it applied to move it to tracking."
                    cta="Open apply queue"
                    to="/saved?view=ready"
                    empty="Apply queue"
                    emptyBody="Save a match to start your apply queue."
                  />
                  <FocusCard
                    tone="rose"
                    eyebrow="Follow up"
                    count={summary.follow_up ?? 0}
                    title="applications waiting 14+ days"
                    body="Check for a response or follow up. Long waits are easy to lose in a long list."
                    cta="Review waiting"
                    to="/applications?view=follow_up"
                    empty="Follow-ups"
                    emptyBody="Applications appear here after 14 days in flight."
                  />
                </div>
                {queueClear ? (
                  <p className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-900">
                    Queue clear. Review the full list or add another source when you want more.
                  </p>
                ) : null}
              </section>

              <section className="rounded-lg border border-line bg-white p-4 shadow-card">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-bold text-slate-900">This week</h2>
                    <p className="text-xs text-slate-500">Momentum from your own history, not a streak.</p>
                  </div>
                  <Link to="/applications?view=insights" className="text-xs font-medium text-brand-700 underline">
                    See response analytics
                  </Link>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-4 sm:grid-cols-4">
                  <Metric label="Applied" value={summary.applied_last_7_days ?? 0} tone="brand" />
                  <Metric label="Responses" value={summary.responded_last_7_days ?? 0} tone="success" />
                  <Metric label="Interviewing" value={summary.interviewing ?? 0} tone="warning" />
                  <Metric label="Offers" value={summary.offers ?? 0} tone="success" />
                </div>
              </section>

              <SearchPlan state={workflowState} compact />
            </>
          ) : (
            <SearchPlan state={workflowState} />
          )}

          <section
            aria-label="Matches to review"
            className="rounded-lg border border-line bg-white p-4 shadow-card"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-base font-bold text-slate-900">Look at these next</h2>
                <p className="mt-1 text-xs text-slate-500">
                  The strongest matches right now, not a filter of everything collected.
                </p>
              </div>
              <Link to="/discover" className={buttonClass("secondary")}>
                See all matches
              </Link>
            </div>
            {jobs.length === 0 ? (
              <div className="mt-3">
                <EmptyState
                  icon={hasSources ? "search" : "briefcase"}
                  tone={hasSources ? "brand" : "amber"}
                  heading={hasSources ? "No matches yet" : "No jobs collected yet"}
                  body={
                    step.stage === "profile"
                      ? "Import a resume so fit scores can rank what you collect. Nothing leaves this computer."
                      : totalMatches === 0
                        ? step.reason
                        : hasSources
                          ? "Your sources have not produced a ranked match yet. Crawl one to collect postings."
                          : "Add a company careers page or job board and crawl it once. Matching starts as postings arrive."
                  }
                  action={
                    step.stage === "profile" ? (
                      <Link to={step.to} className={buttonClass("primary")}>
                        {step.cta}
                      </Link>
                    ) : step.stage === "review" && totalMatches > 0 ? (
                      <Link to={reviewTo} className={buttonClass("primary")}>
                        Review matches
                      </Link>
                    ) : (
                      <CollectJobsAction onComplete={load} />
                    )
                  }
                />
              </div>
            ) : (
              <ul className="mt-3 divide-y divide-slate-100">
                {jobs.slice(0, 3).map((job) => (
                  <li key={job.job_id} className="flex flex-wrap items-center gap-3 py-3">
                    <CompanyAvatar name={job.company_name} />
                    <div className="min-w-0 flex-1">
                      <Link
                        to={`/jobs/${job.job_id}`}
                        className="font-semibold text-slate-900 hover:text-brand-700 hover:underline"
                      >
                        {job.title}
                      </Link>
                      <p className="mt-0.5 text-xs text-slate-500">
                        {[job.company_name, job.location_text]
                          .filter(Boolean)
                          .join(" · ") || "Company not recorded"}
                        {job.first_seen_at ? ` · ${ageLabel(job.first_seen_at)}` : ""}
                      </p>
                    </div>
                    <ScoreMeter value={job.fit_score} />
                    <RiskBadge
                      riskClass={job.risk_class}
                      confidence={job.risk_confidence}
                      label={job.risk_label}
                      compact
                    />
                  </li>
                ))}
              </ul>
            )}
          </section>

          {hiddenCount > 0 ? (
            <p className="text-xs text-slate-500">
              {hiddenCount} more hidden by your policy across{" "}
              {rankWindow === null ? "the ranking window" : `the ${rankWindow} most recent postings`}.
            </p>
          ) : null}

          <p className="text-xs text-slate-500">
            Next: {step.cta.toLowerCase()} →{" "}
            <Link to={step.to} className="font-medium text-brand-700 underline">
              {step.stage}
            </Link>
          </p>
        </>
      )}
    </div>
  );
}
