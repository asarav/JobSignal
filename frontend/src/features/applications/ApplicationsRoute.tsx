import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Button, buttonClass } from "../../components/Button";
import { CompanyAvatar } from "../../components/CompanyAvatar";
import { EmptyState } from "../../components/EmptyState";
import { Metric } from "../../components/Metric";
import { PageHeader } from "../../components/PageHeader";
import { Pagination } from "../../components/Pagination";
import { StatusChip } from "../../components/StatusChip";
import { Tabs } from "../../components/Tabs";
import { useToast } from "../../components/Toast";
import { WorkflowNextStepBar } from "../../components/WorkflowNextStepBar";
import { workerClient, WorkerError } from "../../services/workerClient";
import { useWorkflowState } from "../workflow/useWorkflowState";
import type {
  ApplicationsGetSuppressionsResponseSuppressionInfo,
  ApplicationsListResponseApplicationInfo,
  ApplicationsListResponseApplicationSummary,
} from "../../types/generated";

type ApplicationView =
  | "all"
  | "follow_up"
  | "active"
  | "interviewing"
  | "offers"
  | "closed"
  | "insights"
  | "hidden";

type ApplicationSort = "newest" | "oldest" | "updated";

const VIEWS: ApplicationView[] = [
  "all",
  "follow_up",
  "active",
  "interviewing",
  "offers",
  "closed",
  "insights",
  "hidden",
];

const STATUSES = [
  "interested",
  "saved",
  "applied",
  "acknowledged",
  "recruiter_contacted",
  "interviewing",
  "rejected",
  "withdrawn",
  "offered",
  "accepted",
];

const STATUS_LABELS: Record<string, string> = {
  interested: "Interested",
  saved: "Saved (tracking)",
  applied: "Applied",
  acknowledged: "Acknowledged",
  recruiter_contacted: "Recruiter contacted",
  interviewing: "Interviewing",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  offered: "Offer",
  accepted: "Accepted",
};

const OUTCOME_OPTIONS = [
  { kind: "acknowledged", label: "Acknowledged" },
  { kind: "recruiter_contact", label: "Recruiter contacted" },
  { kind: "interview", label: "Interview" },
  { kind: "rejection", label: "Rejection" },
  { kind: "offer", label: "Offer" },
  { kind: "no_response", label: "No response (not a risk signal)" },
];

const REASON_LABELS: Record<string, string> = {
  exact_repost_of_applied: "Same job reposted",
  similar_role_cooldown: "Similar role you applied to",
  company_wide_cooldown: "Company-wide cooldown",
};

const EMPTY_SUMMARY: ApplicationsListResponseApplicationSummary = {
  total: 0,
  applied: 0,
  active: 0,
  interviewing: 0,
  offers: 0,
  closed: 0,
  follow_up: 0,
  applied_last_7_days: 0,
  responded: 0,
  responded_last_7_days: 0,
};

interface PolicyForm {
  exact_repost_suppress: boolean;
  similar_role_cooldown_days: number;
  similar_role_threshold: number;
  company_wide_cooldown_enabled: boolean;
  company_wide_cooldown_days: number;
}

function toPolicy(raw: Record<string, unknown>): PolicyForm {
  const number = (key: string, fallback: number) =>
    typeof raw[key] === "number" ? (raw[key] as number) : fallback;
  return {
    exact_repost_suppress: raw.exact_repost_suppress !== false,
    similar_role_cooldown_days: number("similar_role_cooldown_days", 180),
    similar_role_threshold: number("similar_role_threshold", 0.75),
    company_wide_cooldown_enabled: raw.company_wide_cooldown_enabled === true,
    company_wide_cooldown_days: number("company_wide_cooldown_days", 90),
  };
}

function formatDate(value?: string | null): string {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
}

function message(exc: unknown): string {
  return exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc);
}

function eventText(event: Record<string, unknown> | undefined): string {
  if (!event) return "No events recorded yet.";
  const kind = String(event.kind ?? event.event_type ?? "update").replaceAll("_", " ");
  const at = event.occurred_at ?? event.created_at;
  return `${kind}${at ? ` · ${formatDate(String(at))}` : ""}`;
}

function Timeline({ application }: { application: ApplicationsListResponseApplicationInfo }) {
  const events = application.events ?? [];
  const outcomes = application.outcomes ?? [];
  if (events.length === 0 && outcomes.length === 0) {
    return <p className="text-xs text-slate-500">No timeline events for this application yet.</p>;
  }
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Events ({events.length})
        </h4>
        <ol className="mt-2 space-y-2 border-l border-line pl-3">
          {events.map((event, index) => (
            <li key={index} className="relative text-xs text-slate-600">
              <span className="absolute -left-[17px] top-1 h-2 w-2 rounded-full bg-brand-400" aria-hidden="true" />
              <p className="font-medium text-slate-700">{eventText(event)}</p>
              {typeof event.note === "string" && event.note ? (
                <p className="text-slate-500">{event.note}</p>
              ) : null}
            </li>
          ))}
          {events.length === 0 ? <li className="text-slate-500">No events recorded.</li> : null}
        </ol>
      </div>
      <div>
        <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Outcomes ({outcomes.length})
        </h4>
        <ul className="mt-2 space-y-1.5">
          {outcomes.map((outcome, index) => (
            <li key={index} className="text-xs text-slate-600">
              <span className="font-medium text-slate-700">
                {String(outcome.kind ?? "outcome").replaceAll("_", " ")}
              </span>{" "}
              · {formatDate(String(outcome.occurred_at ?? ""))}
              {outcome.confirmed === false ? " · unconfirmed" : ""}
            </li>
          ))}
          {outcomes.length === 0 ? <li className="text-slate-500">No outcomes recorded.</li> : null}
        </ul>
      </div>
    </div>
  );
}

export function ApplicationsRoute() {
  const workflow = useWorkflowState();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const rawView = (params.get("view") ?? "all") as ApplicationView;
  const view: ApplicationView = VIEWS.includes(rawView) ? rawView : "all";
  const query = params.get("q") ?? "";
  const rawSort = params.get("sort");
  const sort: ApplicationSort =
    rawSort === "oldest" || rawSort === "updated" ? rawSort : "newest";
  const setView = (next: ApplicationView) => {
    const nextParams = new URLSearchParams(params);
    if (next === "all") nextParams.delete("view");
    else nextParams.set("view", next);
    setParams(nextParams);
  };
  const setQuery = (next: string) => {
    const nextParams = new URLSearchParams(params);
    if (next) nextParams.set("q", next);
    else nextParams.delete("q");
    setParams(nextParams, { replace: true });
  };
  const setSort = (next: ApplicationSort) => {
    const nextParams = new URLSearchParams(params);
    if (next === "newest") nextParams.delete("sort");
    else nextParams.set("sort", next);
    setParams(nextParams);
  };
  const [applications, setApplications] = useState<ApplicationsListResponseApplicationInfo[]>([]);
  const [summary, setSummary] = useState<ApplicationsListResponseApplicationSummary>(EMPTY_SUMMARY);
  const [total, setTotal] = useState(0);
  const [suppressions, setSuppressions] = useState<
    ApplicationsGetSuppressionsResponseSuppressionInfo[]
  >([]);
  const [policy, setPolicy] = useState<PolicyForm | null>(null);
  const [analytics, setAnalytics] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [flashId, setFlashId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  const metaView = view === "insights" || view === "hidden";

  const load = useCallback(
    async (offset = 0, append = false) => {
      try {
        const result = await workerClient.listApplications({
          view: metaView ? "all" : view,
          query: metaView ? undefined : query.trim() || undefined,
          sort,
          limit: metaView ? 1 : 25,
          offset: metaView ? 0 : offset,
        });
        setSummary(result.summary ?? EMPTY_SUMMARY);
        if (!metaView) {
          setApplications((current) =>
            append ? [...current, ...result.applications] : result.applications,
          );
          setTotal(result.total ?? result.applications.length);
        }
        setError(null);
      } catch (exc) {
        setError(message(exc));
      } finally {
        setLoaded(true);
      }
    },
    [view, metaView, query, sort],
  );

  useEffect(() => {
    void load(0, false);
  }, [load]);

  const loadMeta = useCallback(async () => {
    try {
      const [suppressed, stored] = await Promise.all([
        workerClient.getSuppressions(),
        workerClient.getSuppressionPolicy(),
      ]);
      setSuppressions(suppressed.suppressions);
      setPolicy(toPolicy(stored.policy));
    } catch (exc) {
      setError(message(exc));
    }
  }, []);

  useEffect(() => {
    void loadMeta();
  }, [loadMeta]);

  useEffect(() => {
    if (view !== "insights" || analytics) return;
    void workerClient
      .getAnalytics({ days: 365 })
      .then((result) => setAnalytics(result as unknown as Record<string, unknown>))
      .catch((exc) => setError(message(exc)));
  }, [view, analytics]);

  const recordOutcome = async (applicationId: string, kind: string) => {
    if (!kind) return;
    setBusyId(applicationId);
    setError(null);
    try {
      await workerClient.recordApplicationOutcome({ application_id: applicationId, kind });
      toast.push("Outcome recorded.", { tone: "success" });
      setFlashId(applicationId);
      window.setTimeout(() => {
        setFlashId((current) => (current === applicationId ? null : current));
      }, 900);
      await load(0, false);
      workflow.refresh();
      setAnalytics(null);
    } catch (exc) {
      setError(message(exc));
      toast.push("Could not record that outcome.", { tone: "danger" });
    } finally {
      setBusyId(null);
    }
  };

  const changeStatus = async (applicationId: string, status: string) => {
    setBusyId(applicationId);
    setError(null);
    try {
      await workerClient.updateApplicationStatus({ application_id: applicationId, status });
      toast.push("Status updated.", { tone: "success" });
      setFlashId(applicationId);
      window.setTimeout(() => {
        setFlashId((current) => (current === applicationId ? null : current));
      }, 900);
      await load(0, false);
      workflow.refresh();
    } catch (exc) {
      setError(message(exc));
      toast.push("Could not update that status.", { tone: "danger" });
    } finally {
      setBusyId(null);
    }
  };

  const restore = async (jobId: string, applicationId: string) => {
    setBusy(true);
    setError(null);
    try {
      await workerClient.restoreSuppressedJob({ job_id: jobId, application_id: applicationId });
      toast.push("Role restored. Automation will not hide it again.", { tone: "success" });
      await loadMeta();
    } catch (exc) {
      setError(message(exc));
    } finally {
      setBusy(false);
    }
  };

  const savePolicy = async () => {
    if (!policy) return;
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      const result = await workerClient.updateSuppressionPolicy({
        policy: { ...policy } as unknown as Record<string, unknown>,
      });
      setPolicy(toPolicy(result.policy));
      setSaved(true);
      toast.push("Suppression rules saved.", { tone: "success" });
      await loadMeta();
    } catch (exc) {
      setError(message(exc));
    } finally {
      setBusy(false);
    }
  };

  const stats = {
    total: summary.total ?? 0,
    applied: summary.applied ?? 0,
    active: summary.active ?? 0,
    interviewing: summary.interviewing ?? 0,
    offers: summary.offers ?? 0,
    closed: summary.closed ?? 0,
    follow_up: summary.follow_up ?? 0,
    applied_last_7_days: summary.applied_last_7_days ?? 0,
    responded: summary.responded ?? 0,
  };
  const responseRate =
    stats.applied > 0 ? `${Math.round((stats.responded / stats.applied) * 100)}%` : "—";

  return (
    <div className="space-y-5">
      <PageHeader
        title="Applications"
        description="What you have sent, how it went, and what your history hides. You submit every application yourself."
      />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      ) : null}

      <section className="rounded-lg border border-line bg-white p-4 shadow-card" aria-label="Application summary">
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          <Metric label="Applied" value={stats.applied} tone="brand" />
          <Metric
            label="Response rate"
            value={responseRate}
            note={`${stats.responded} of ${stats.applied} with a response`}
            tone="success"
          />
          <Metric label="In flight" value={stats.active} tone="warning" />
          <Metric label="Interviews" value={stats.interviewing} tone="warning" />
          <Metric label="Offers" value={stats.offers} tone="success" />
        </div>
      </section>

      <Tabs
        label="Application views"
        active={view}
        onChange={(next) => setView(next as ApplicationView)}
        items={[
          { id: "all", label: "All", count: stats.total },
          { id: "follow_up", label: "Waiting 14+ days", count: stats.follow_up },
          { id: "active", label: "In flight", count: stats.active },
          { id: "interviewing", label: "Interviews", count: stats.interviewing },
          { id: "offers", label: "Offers", count: stats.offers },
          { id: "closed", label: "Closed", count: stats.closed },
          { id: "insights", label: "Insights" },
          { id: "hidden", label: "Hidden", count: suppressions.length },
        ]}
      />

      {metaView ? (
        view === "insights" ? (
          <section className="rounded-lg border border-line bg-white p-4 shadow-card">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h2 className="text-base font-semibold text-slate-900">Response analytics</h2>
                <p className="mt-1 text-xs text-slate-500">
                  Associations from your own history. Groups with fewer than{" "}
                  {String(analytics?.min_sample ?? 5)} applications hide their rate, and nothing
                  here changes ranking automatically.
                </p>
              </div>
              <Button
                disabled={busy}
                onClick={() => {
                  setAnalytics(null);
                  setBusy(true);
                  void workerClient
                    .getAnalytics({ days: 365 })
                    .then((result) => setAnalytics(result as unknown as Record<string, unknown>))
                    .catch((exc) => setError(message(exc)))
                    .finally(() => setBusy(false));
                }}
              >
                Refresh
              </Button>
            </div>
            {!analytics ? (
              <p className="mt-2 text-sm text-slate-500">Loading analytics…</p>
            ) : ((analytics.groups ?? []) as Record<string, unknown>[]).length === 0 ? (
              <p className="mt-3 rounded-lg border border-line bg-slate-50 p-3 text-sm text-slate-600">
                Not enough history to compare groups yet. Record outcomes as responses arrive.
              </p>
            ) : (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-left text-xs text-slate-700">
                  <thead>
                    <tr className="text-slate-500">
                      <th className="py-1">Dimension</th>
                      <th>Group</th>
                      <th>n</th>
                      <th>Response rate</th>
                      <th>95% interval</th>
                    </tr>
                  </thead>
                  <tbody>
                    {((analytics.groups ?? []) as Record<string, unknown>[]).map((row, index) => {
                      const insufficient = row.insufficient === true;
                      const interval = row.interval as number[] | null | undefined;
                      return (
                        <tr key={index} className="border-t border-line">
                          <td className="py-1">{String(row.group).replaceAll("_", " ")}</td>
                          <td>{String(row.key)}</td>
                          <td className="tabular">{String(row.n)}</td>
                          <td className="tabular">
                            {insufficient
                              ? "hidden (small sample)"
                              : typeof row.rate === "number"
                                ? row.rate.toFixed(2)
                                : "—"}
                          </td>
                          <td className="tabular">
                            {insufficient || !interval
                              ? "—"
                              : `${interval[0].toFixed(2)}–${interval[1].toFixed(2)}`}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </section>
        ) : (
          <div className="space-y-4">
            <section className="rounded-lg border border-line bg-white p-4 shadow-card">
              <h2 className="text-base font-semibold text-slate-900">Hidden roles</h2>
              <p className="mt-1 text-xs text-slate-500">
                Roles hidden because of your history. Restoring a role keeps it visible for good —
                automation will not hide it again.
              </p>
              {suppressions.length === 0 ? (
                <p className="mt-3 rounded-lg border border-line bg-slate-50 p-3 text-sm text-slate-600">
                  Nothing is hidden right now.
                </p>
              ) : (
                <ul className="mt-2 divide-y divide-slate-100">
                  {suppressions.map((row, index) => (
                    <li key={`${row.job_id}-${row.application_id}-${index}`} className="py-3 text-sm">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <span className="font-medium text-slate-900">
                          {row.job_title ?? row.job_id}
                        </span>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
                          {row.state === "active"
                            ? "Hidden"
                            : row.state === "expired"
                              ? "Expired"
                              : "Restored"}
                        </span>
                      </div>
                      <p className="mt-1 text-slate-600">{row.explanation}</p>
                      <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                        <span>{REASON_LABELS[row.reason] ?? row.reason}</span>
                        {row.expires_at ? <span>Until {formatDate(row.expires_at)}</span> : null}
                        {row.state !== "restored" ? (
                          <Button
                            variant="secondary"
                            disabled={busy}
                            onClick={() => void restore(row.job_id, row.application_id)}
                          >
                            Restore
                          </Button>
                        ) : null}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {policy ? (
              <section className="rounded-lg border border-line bg-white p-4 shadow-card">
                <h2 className="text-base font-semibold text-slate-900">Suppression rules</h2>
                <p className="mt-1 text-xs text-slate-500">
                  Only an application that reached “Applied” starts a cooldown. Saving, dismissing
                  or marking interested never hides anything.
                </p>
                <div className="mt-3 grid gap-3 text-sm text-slate-700 sm:grid-cols-2">
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={policy.exact_repost_suppress}
                      onChange={(event) =>
                        setPolicy({ ...policy, exact_repost_suppress: event.target.checked })
                      }
                    />
                    Hide the same job when it is reposted
                  </label>
                  <label className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={policy.company_wide_cooldown_enabled}
                      onChange={(event) =>
                        setPolicy({
                          ...policy,
                          company_wide_cooldown_enabled: event.target.checked,
                        })
                      }
                    />
                    Hide every role at the company
                  </label>
                  <label className="flex items-center gap-2">
                    Similar role cooldown (days)
                    <input
                      type="number"
                      min={0}
                      max={3650}
                      value={policy.similar_role_cooldown_days}
                      onChange={(event) =>
                        setPolicy({
                          ...policy,
                          similar_role_cooldown_days: Number(event.target.value),
                        })
                      }
                      className="w-24 rounded border border-line px-2 py-1"
                    />
                  </label>
                  <label className="flex items-center gap-2">
                    Similarity threshold
                    <input
                      type="number"
                      min={0}
                      max={1}
                      step={0.05}
                      value={policy.similar_role_threshold}
                      onChange={(event) =>
                        setPolicy({
                          ...policy,
                          similar_role_threshold: Number(event.target.value),
                        })
                      }
                      className="w-24 rounded border border-line px-2 py-1"
                    />
                  </label>
                  <label className="flex items-center gap-2">
                    Company-wide cooldown (days)
                    <input
                      type="number"
                      min={0}
                      max={3650}
                      value={policy.company_wide_cooldown_days}
                      onChange={(event) =>
                        setPolicy({
                          ...policy,
                          company_wide_cooldown_days: Number(event.target.value),
                        })
                      }
                      className="w-24 rounded border border-line px-2 py-1"
                    />
                  </label>
                </div>
                <div className="mt-3 flex items-center gap-3">
                  <Button variant="primary" disabled={busy} onClick={() => void savePolicy()}>
                    Save rules
                  </Button>
                  {saved ? <span className="text-xs text-emerald-700">Saved.</span> : null}
                </div>
              </section>
            ) : null}
          </div>
        )
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 rounded-lg border border-line bg-white p-3 shadow-card">
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search title or company…"
              aria-label="Search applications"
              className="min-w-[12rem] flex-1 rounded-md border border-line px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
            />
            <select
              aria-label="Sort applications"
              value={sort}
              onChange={(event) => setSort(event.target.value as ApplicationSort)}
              className="rounded-md border border-line px-2 py-2 text-sm"
            >
              <option value="newest">Newest first</option>
              <option value="oldest">Oldest first</option>
              <option value="updated">Recently updated</option>
            </select>
            <span className="tabular text-xs text-slate-500">
              {stats.applied} applied · {stats.applied_last_7_days} this week
            </span>
          </div>

          <section className="rounded-lg border border-line bg-white p-4 shadow-card">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-base font-semibold text-slate-900">
                Application history
                <span className="tabular ml-2 font-normal text-slate-500">({total})</span>
              </h2>
              <p className="text-xs text-slate-500">
                {view === "follow_up"
                  ? "Waiting 14+ days, not “no response”. Check for a reply or follow up."
                  : "Expand a row for its full timeline."}
              </p>
            </div>

            {!loaded ? (
              <p className="mt-3 text-sm text-slate-500">Loading applications…</p>
            ) : applications.length === 0 ? (
              <div className="mt-3">
                <EmptyState
                  icon={query ? "search" : "briefcase"}
                  tone={query ? "amber" : "brand"}
                  heading={query ? "No applications match this search" : "No applications in this view"}
                  body={
                    query
                      ? "Try a shorter search or switch to All."
                      : view === "follow_up"
                        ? "Nothing has been waiting 14+ days. That is a clean queue, not a missing result."
                        : "Track starts with your first application. Mark a saved role applied after you send it."
                  }
                  action={
                    query ? (
                      <Button onClick={() => setQuery("")}>Clear search</Button>
                    ) : (
                      <Link to="/saved?view=ready" className={buttonClass("primary")}>
                        Open apply queue
                      </Link>
                    )
                  }
                />
              </div>
            ) : (
              <>
                <ul className="mt-2 divide-y divide-slate-100">
                  {applications.map((application) => (
                    <li
                      key={application.application_id}
                      className={`py-3 ${flashId === application.application_id ? "animate-row-flash" : ""}`}
                    >
                      <div className="flex flex-wrap items-start gap-3">
                        <CompanyAvatar name={application.company_name} />
                        <div className="min-w-0 flex-1">
                          <div className="flex flex-wrap items-center gap-2">
                            <Link
                              to={`/jobs/${application.job_id}`}
                              className="font-semibold text-slate-900 transition hover:text-brand-700 hover:underline"
                            >
                              {application.job_title}
                            </Link>
                            <StatusChip status={application.status} />
                          </div>
                          <p className="mt-0.5 text-xs text-slate-500">
                            {application.company_name || "Company not recorded"} · applied{" "}
                            {formatDate(application.applied_at)} · updated{" "}
                            {formatDate(application.updated_at)}
                          </p>
                          <p className="mt-0.5 text-xs text-slate-500">
                            Latest: {eventText(application.events?.[0])} ·{" "}
                            {(application.outcomes ?? []).length} outcomes
                          </p>
                        </div>
                        <div className="flex w-full flex-wrap items-center gap-2 pl-[3.25rem] sm:w-auto sm:pl-0">
                          <label className="flex items-center gap-1 text-xs text-slate-500">
                            <span className="sr-only">Quick outcome for {application.job_title}</span>
                            <select
                              aria-label={`Quick outcome for ${application.job_title}`}
                              disabled={busyId === application.application_id}
                              defaultValue=""
                              onChange={(event) => {
                                void recordOutcome(
                                  application.application_id,
                                  event.target.value,
                                );
                                event.target.value = "";
                              }}
                              className="rounded border border-line px-2 py-1 text-xs text-slate-700"
                            >
                              <option value="">Record outcome…</option>
                              {OUTCOME_OPTIONS.map((option) => (
                                <option key={option.kind} value={option.kind}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                          </label>
                        </div>
                      </div>
                      <details className="mt-2 rounded-md border border-line bg-slate-50/60 p-2">
                        <summary className="cursor-pointer text-xs font-medium text-slate-600">
                          Timeline and status
                        </summary>
                        <div className="mt-3 space-y-3">
                          <Timeline application={application} />
                          <label className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                            Status
                            <select
                              aria-label={`Status for ${application.job_title}`}
                              value={application.status}
                              disabled={busyId === application.application_id}
                              onChange={(event) =>
                                void changeStatus(
                                  application.application_id,
                                  event.target.value,
                                )
                              }
                              className="rounded border border-line px-2 py-1 text-xs text-slate-700"
                            >
                              {STATUSES.map((status) => (
                                <option key={status} value={status}>
                                  {STATUS_LABELS[status] ?? status}
                                </option>
                              ))}
                            </select>
                          </label>
                        </div>
                      </details>
                    </li>
                  ))}
                </ul>
                <Pagination
                  shown={applications.length}
                  total={total}
                  loading={busy}
                  noun="applications"
                  onLoadMore={() => void load(applications.length, true)}
                />
              </>
            )}
          </section>
        </>
      )}

      {!workflow.loading && !workflow.error ? (
        <WorkflowNextStepBar state={workflow} />
      ) : null}

      <p className="text-xs text-slate-500">
        Looking for a saved role you have not applied to yet?{" "}
        <Link to="/saved?view=ready" className="whitespace-nowrap underline">
          Open the apply queue
        </Link>
        .
      </p>
    </div>
  );
}
