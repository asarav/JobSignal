import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Button, buttonClass } from "../../components/Button";
import { CompanyAvatar } from "../../components/CompanyAvatar";
import { EmptyState } from "../../components/EmptyState";
import { PageHeader } from "../../components/PageHeader";
import { Pagination } from "../../components/Pagination";
import { RiskBadge } from "../../components/RiskBadge";
import { ScoreMeter } from "../../components/ScoreMeter";
import { Tabs } from "../../components/Tabs";
import { useToast } from "../../components/Toast";
import { WorkflowNextStepBar } from "../../components/WorkflowNextStepBar";
import { workerClient, WorkerError } from "../../services/workerClient";
import { useWorkflowState } from "../workflow/useWorkflowState";
import { useLiveRefresh } from "../../hooks/useLiveRefresh";
import type { JobsListSavedResponseJobSummary } from "../../types/generated";

type SavedView = "ready" | "all";
type SavedSort = "rank" | "newest" | "company";

function message(exc: unknown): string {
  return exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc);
}

function ageLabel(value?: string | null): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  const days = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 86_400_000));
  return days === 0 ? "new today" : `${days}d old`;
}

function SavedRow({
  job,
  busy,
  flash = false,
  onApply,
  onRemove,
}: {
  job: JobsListSavedResponseJobSummary;
  busy: boolean;
  flash?: boolean;
  onApply: () => void;
  onRemove: () => void;
}) {
  return (
    <li
      className={`flex flex-wrap items-start gap-3 py-3 transition-colors hover:bg-slate-50/70 ${
        flash ? "animate-row-flash" : ""
      }`}
    >
      <CompanyAvatar name={job.company_name} />
      <div className="min-w-0 flex-1">
        <Link
          className="font-semibold text-slate-900 transition hover:text-brand-700 hover:underline"
          to={`/jobs/${job.job_id}`}
        >
          {job.title}
        </Link>
        <p className="text-xs text-slate-500">
          {[job.company_name, job.location_text, job.workplace_type]
            .filter((value) => value && value !== "unknown")
            .join(" · ") || "Company and location not recorded"}
          {job.first_seen_at ? ` · ${ageLabel(job.first_seen_at)}` : ""}
        </p>
        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <ScoreMeter value={job.fit_score} />
          <RiskBadge
            riskClass={job.risk_class}
            confidence={job.risk_confidence}
            label={job.risk_label}
            compact
          />
          {job.tracked ? (
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 font-medium text-emerald-700">
              Already tracked
            </span>
          ) : (
            <span className="rounded-full bg-amber-50 px-2 py-0.5 font-medium text-amber-800">
              Ready
            </span>
          )}
        </div>
      </div>
      <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
        <Link to={`/jobs/${job.job_id}`} className={buttonClass("secondary")}>
          Open
        </Link>
        {job.tracked ? (
          <Link to="/applications" className={buttonClass("quiet")}>
            Applied ✓ · Track
          </Link>
        ) : (
          <Button variant="primary" disabled={busy} onClick={onApply}>
            Mark as applied
          </Button>
        )}
        <Button variant="quiet" disabled={busy} onClick={onRemove}>
          Remove
        </Button>
      </div>
    </li>
  );
}

export function SavedRoute() {
  const workflow = useWorkflowState();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const view: SavedView = params.get("view") === "all" ? "all" : "ready";
  const query = params.get("q") ?? "";
  const rawSort = params.get("sort");
  const sort: SavedSort = rawSort === "newest" || rawSort === "company" ? rawSort : "rank";
  const setView = (next: SavedView) => {
    const nextParams = new URLSearchParams(params);
    if (next === "all") nextParams.set("view", "all");
    else nextParams.delete("view");
    setParams(nextParams);
  };
  const setQuery = (next: string) => {
    const nextParams = new URLSearchParams(params);
    if (next) nextParams.set("q", next);
    else nextParams.delete("q");
    setParams(nextParams, { replace: true });
  };
  const setSort = (next: SavedSort) => {
    const nextParams = new URLSearchParams(params);
    if (next === "rank") nextParams.delete("sort");
    else nextParams.set("sort", next);
    setParams(nextParams);
  };
  const [jobs, setJobs] = useState<JobsListSavedResponseJobSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [readyTotal, setReadyTotal] = useState(0);
  const [allTotal, setAllTotal] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [flashId, setFlashId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(
    async (offset = 0, append = false) => {
      try {
        const [page, ready, all] = await Promise.all([
          workerClient.listSavedJobs({
            view,
            query: query.trim() || undefined,
            sort,
            limit: 25,
            offset,
          }),
          workerClient.listSavedJobs({ view: "ready", limit: 1 }),
          workerClient.listSavedJobs({ view: "all", limit: 1 }),
        ]);
        setJobs((current) => (append ? [...current, ...page.jobs] : page.jobs));
        setTotal(page.total ?? page.jobs.length);
        setReadyTotal(ready.total ?? ready.jobs.length);
        setAllTotal(all.total ?? all.jobs.length);
        setError(null);
      } catch (exc) {
        setError(message(exc));
      } finally {
        setLoaded(true);
      }
    },
    [view, query, sort],
  );

  useEffect(() => {
    void load(0, false);
  }, [load]);
  useLiveRefresh(() => void load(0, false));

  const markApplied = async (job: JobsListSavedResponseJobSummary) => {
    setBusyId(job.job_id);
    const previous = jobs;
    if (view === "ready") {
      setJobs((current) => current.filter((row) => row.job_id !== job.job_id));
      setTotal((value) => Math.max(0, value - 1));
      setReadyTotal((value) => Math.max(0, value - 1));
    } else {
      setJobs((current) =>
        current.map((row) => (row.job_id === job.job_id ? { ...row, tracked: true } : row)),
      );
    }
    try {
      await workerClient.createApplication({ job_id: job.job_id, status: "applied" });
      toast.push(`Marked “${job.title}” as applied.`, {
        tone: "success",
        action: { label: "Open Applications", to: "/applications" },
      });
      setFlashId(job.job_id);
      window.setTimeout(() => {
        setFlashId((current) => (current === job.job_id ? null : current));
      }, 900);
      workflow.refresh();
      setError(null);
    } catch (exc) {
      setJobs(previous);
      setTotal(view === "ready" ? previous.length : total);
      setReadyTotal((value) => value + (view === "ready" ? 1 : 0));
      setError(message(exc));
      toast.push("Could not mark this application. Reverted.", { tone: "danger" });
    } finally {
      setBusyId(null);
    }
  };

  const remove = async (job: JobsListSavedResponseJobSummary) => {
    setBusyId(job.job_id);
    try {
      await workerClient.setJobUserState({ job_id: job.job_id, state: "new" });
      toast.push(`Removed “${job.title}” from saved.`, { tone: "info" });
      await load(0, false);
      workflow.refresh();
    } catch (exc) {
      setError(message(exc));
      toast.push("Could not remove this job.", { tone: "danger" });
    } finally {
      setBusyId(null);
    }
  };

  const refreshAll = async () => {
    setBusy(true);
    await load(0, false);
    setBusy(false);
  };

  const readyJobs = jobs.filter((job) => !job.tracked);
  const trackedJobs = jobs.filter((job) => job.tracked);
  const hasAnySaved = allTotal > 0;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Saved"
        description="Your shortlist as an apply queue. Apply yourself, then record it so the role moves to tracking."
      />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      ) : null}

      {!loaded ? (
        <p className="text-sm text-slate-500">Loading saved jobs…</p>
      ) : !hasAnySaved ? (
        <EmptyState
          icon="bookmark"
          tone="brand"
          heading="Nothing shortlisted yet"
          body="Open a match on Find jobs and save the roles worth pursuing. They collect here, ready for you to apply."
          action={
            <Link to="/discover" className={buttonClass("primary")}>
              Find jobs
            </Link>
          }
        />
      ) : (
        <>
          <Tabs
            label="Saved jobs"
            active={view}
            onChange={(next) => setView(next as SavedView)}
            items={[
              { id: "ready", label: `Apply queue (${readyTotal})` },
              { id: "all", label: `All saved (${allTotal})` },
            ]}
          />

          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-white p-3 shadow-card">
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search saved titles or companies…"
              aria-label="Search saved jobs"
              className="min-w-[12rem] flex-1 rounded-md border border-line px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
            <select
              aria-label="Sort saved jobs"
              value={sort}
              onChange={(event) => setSort(event.target.value as SavedSort)}
              className="rounded-md border border-line px-2 py-2 text-sm"
            >
              <option value="rank">Strongest fit</option>
              <option value="newest">Newest</option>
              <option value="company">Company</option>
            </select>
            <span className="tabular text-xs text-slate-500">
              {readyTotal} ready · {allTotal - readyTotal} tracked
            </span>
          </div>

          <section className="rounded-lg border border-line bg-white p-4 shadow-card">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-base font-semibold text-slate-900">
                {view === "ready" ? "Apply queue" : "All saved"}
                <span className="tabular ml-2 font-normal text-slate-500">({total})</span>
              </h2>
              <p className="text-xs text-slate-500">
                {view === "ready"
                  ? "Strongest fit first. Tracked roles leave this queue automatically."
                  : "Every saved role, including ones already in Applications."}
              </p>
            </div>

            {jobs.length === 0 ? (
              <div className="mt-3">
                <EmptyState
                  icon={query ? "search" : "check"}
                  tone={query ? "amber" : "emerald"}
                  heading={query ? "No saved jobs match this search" : "Apply queue is clear"}
                  body={
                    query
                      ? "Try a shorter search, or switch to All saved."
                      : "Every saved role is already tracked. Find more roles or review your applications."
                  }
                  action={
                    query ? (
                      <Button onClick={() => setQuery("")}>Clear search</Button>
                    ) : (
                      <Link to="/discover" className={buttonClass("primary")}>
                        Find more jobs
                      </Link>
                    )
                  }
                />
              </div>
            ) : view === "ready" ? (
              <>
                <ul className="mt-2 divide-y divide-slate-100">
                  {readyJobs.map((job) => (
                    <SavedRow
                      key={job.job_id}
                      job={job}
                      busy={busyId === job.job_id}
                      flash={flashId === job.job_id}
                      onApply={() => void markApplied(job)}
                      onRemove={() => void remove(job)}
                    />
                  ))}
                </ul>
                <Pagination
                  shown={jobs.length}
                  total={total}
                  loading={busy}
                  noun="saved roles"
                  onLoadMore={() => void load(jobs.length, true)}
                />
              </>
            ) : (
              <>
                <ul className="mt-2 divide-y divide-slate-100">
                  {readyJobs.map((job) => (
                    <SavedRow
                      key={job.job_id}
                      job={job}
                      busy={busyId === job.job_id}
                      onApply={() => void markApplied(job)}
                      onRemove={() => void remove(job)}
                    />
                  ))}
                </ul>
                {trackedJobs.length > 0 ? (
                  <details className="mt-3 rounded-lg border border-line bg-slate-50/60 p-3">
                    <summary className="cursor-pointer text-sm font-medium text-slate-700">
                      Already tracked ({trackedJobs.length} on this page)
                    </summary>
                    <ul className="mt-2 divide-y divide-slate-200/70">
                      {trackedJobs.map((job) => (
                        <SavedRow
                          key={job.job_id}
                          job={job}
                          busy={busyId === job.job_id}
                          flash={flashId === job.job_id}
                          onApply={() => void markApplied(job)}
                          onRemove={() => void remove(job)}
                        />
                      ))}
                    </ul>
                  </details>
                ) : null}
                <Pagination
                  shown={jobs.length}
                  total={total}
                  loading={busy}
                  noun="saved roles"
                  onLoadMore={() => void load(jobs.length, true)}
                />
              </>
            )}
          </section>

          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-xs text-slate-500">
              {readyTotal} still waiting on an application.
            </p>
            <Button variant="quiet" disabled={busy} onClick={() => void refreshAll()}>
              Refresh queue
            </Button>
          </div>

          {!workflow.loading && !workflow.error ? (
            <WorkflowNextStepBar state={workflow} />
          ) : null}
        </>
      )}
    </div>
  );
}
