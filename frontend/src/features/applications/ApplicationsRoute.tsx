import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { workerClient, WorkerError } from "../../services/workerClient";
import type {
  ApplicationsGetSuppressionsResponseSuppressionInfo,
  ApplicationsListResponseApplicationInfo,
} from "../../types/generated";

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

export function ApplicationsRoute() {
  const [applications, setApplications] = useState<
    ApplicationsListResponseApplicationInfo[]
  >([]);
  const [suppressions, setSuppressions] = useState<
    ApplicationsGetSuppressionsResponseSuppressionInfo[]
  >([]);
  const [policy, setPolicy] = useState<PolicyForm | null>(null);
  const [analytics, setAnalytics] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);

  const load = useCallback(async () => {
    try {
      const [applied, suppressed, stored] = await Promise.all([
        workerClient.listApplications(),
        workerClient.getSuppressions(),
        workerClient.getSuppressionPolicy(),
      ]);
      setApplications(applied.applications);
      setSuppressions(suppressed.suppressions);
      setPolicy(toPolicy(stored.policy));
      setError(null);
    } catch (exc) {
      setError(message(exc));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const loadAnalytics = useCallback(async () => {
    try {
      const result = await workerClient.getAnalytics({ days: 365 });
      setAnalytics(result as unknown as Record<string, unknown>);
    } catch (exc) {
      setError(message(exc));
    }
  }, []);

  useEffect(() => {
    void loadAnalytics();
  }, [loadAnalytics]);

  const recordOutcome = async (applicationId: string, kind: string) => {
    if (!kind) return;
    setBusy(true);
    setError(null);
    try {
      await workerClient.recordApplicationOutcome({
        application_id: applicationId,
        kind,
      });
      await Promise.all([load(), loadAnalytics()]);
    } catch (exc) {
      setError(message(exc));
    } finally {
      setBusy(false);
    }
  };

  const changeStatus = async (applicationId: string, status: string) => {
    setBusy(true);
    setError(null);
    try {
      await workerClient.updateApplicationStatus({
        application_id: applicationId,
        status,
      });
      await load();
    } catch (exc) {
      setError(message(exc));
    } finally {
      setBusy(false);
    }
  };

  const restore = async (jobId: string, applicationId: string) => {
    setBusy(true);
    setError(null);
    try {
      await workerClient.restoreSuppressedJob({
        job_id: jobId,
        application_id: applicationId,
      });
      await load();
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
      await load();
    } catch (exc) {
      setError(message(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Applications"
        description="Your application history, and the roles hidden because of it. Saving a job for later is the separate bookmark on Discover and Job detail. Nothing here sends an application."
      />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Application history</h2>
        {applications.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            No applications tracked yet. Open a job and mark it saved, interested or
            applied to start a history here.
          </p>
        ) : (
          <ul className="mt-2 divide-y divide-slate-100">
            {applications.map((application) => (
              <li key={application.application_id} className="py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-slate-900">
                    {application.job_title}
                  </span>
                  <select
                    aria-label={`Status for ${application.job_title}`}
                    value={application.status}
                    disabled={busy}
                    onChange={(event) =>
                      void changeStatus(application.application_id, event.target.value)
                    }
                    className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-700"
                  >
                    {STATUSES.map((status) => (
                      <option key={status} value={status}>
                        {STATUS_LABELS[status] ?? status}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="mt-1 text-xs text-slate-500">
                  Applied {formatDate(application.applied_at)} · updated{" "}
                  {formatDate(application.updated_at)} ·{" "}
                  {application.events?.length ?? 0} events
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-600">
                  <label htmlFor={`outcome-${application.application_id}`}>
                    Record an outcome
                  </label>
                  <select
                    id={`outcome-${application.application_id}`}
                    disabled={busy}
                    defaultValue=""
                    onChange={(event) => {
                      void recordOutcome(application.application_id, event.target.value);
                      event.target.value = "";
                    }}
                    className="rounded border border-slate-300 px-2 py-1"
                  >
                    <option value="">— choose —</option>
                    {OUTCOME_OPTIONS.map((option) => (
                      <option key={option.kind} value={option.kind}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                  <span className="text-slate-400">
                    {(application.outcomes ?? []).length} outcomes recorded
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-semibold text-slate-900">Response analytics</h2>
          <button
            type="button"
            disabled={busy}
            onClick={() => void loadAnalytics()}
            className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Refresh
          </button>
        </div>
        <p className="mt-1 text-xs text-slate-500">
          Associations from your own history. Groups with fewer than{" "}
          {String(analytics?.min_sample ?? 5)} applications hide their rate, and nothing
          here changes ranking automatically.
        </p>
        {!analytics ? (
          <p className="mt-2 text-sm text-slate-500">No analytics loaded yet.</p>
        ) : (
          <table className="mt-2 w-full text-left text-xs text-slate-700">
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
              {((analytics.groups ?? []) as Record<string, unknown>[]).map(
                (row, index) => {
                  const insufficient = row.insufficient === true;
                  const interval = row.interval as number[] | null | undefined;
                  return (
                    <tr key={index} className="border-t border-slate-100">
                      <td className="py-1">{String(row.group).replaceAll("_", " ")}</td>
                      <td>{String(row.key)}</td>
                      <td>{String(row.n)}</td>
                      <td>
                        {insufficient
                          ? "hidden (small sample)"
                          : typeof row.rate === "number"
                            ? row.rate.toFixed(2)
                            : "—"}
                      </td>
                      <td>
                        {insufficient || !interval
                          ? "—"
                          : `${interval[0].toFixed(2)}–${interval[1].toFixed(2)}`}
                      </td>
                    </tr>
                  );
                },
              )}
            </tbody>
          </table>
        )}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Hidden roles</h2>
        <p className="mt-1 text-xs text-slate-500">
          Roles hidden because of your history. Restoring a role keeps it visible for
          good — automation will not hide it again.
        </p>
        {suppressions.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">Nothing is hidden right now.</p>
        ) : (
          <ul className="mt-2 divide-y divide-slate-100">
            {suppressions.map((row, index) => (
              <li
                key={`${row.job_id}-${row.application_id}-${index}`}
                className="py-3 text-sm"
              >
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
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void restore(row.job_id, row.application_id)}
                      className="rounded border border-slate-300 px-2 py-1 font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                    >
                      Restore
                    </button>
                  ) : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      {policy ? (
        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-lg font-semibold text-slate-900">Suppression rules</h2>
          <p className="mt-1 text-xs text-slate-500">
            Only an application that reached “Applied” starts a cooldown. Saving,
            dismissing or marking interested never hides anything.
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
                className="w-24 rounded border border-slate-300 px-2 py-1"
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
                className="w-24 rounded border border-slate-300 px-2 py-1"
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
                className="w-24 rounded border border-slate-300 px-2 py-1"
              />
            </label>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <button
              type="button"
              disabled={busy}
              onClick={() => void savePolicy()}
              className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
            >
              Save rules
            </button>
            {saved ? (
              <span className="text-xs text-emerald-700">
                Saved. Hidden roles were re-checked.
              </span>
            ) : null}
          </div>
        </section>
      ) : null}
    </div>
  );
}
