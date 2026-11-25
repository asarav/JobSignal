import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Button, buttonClass } from "../../components/Button";
import { Disclosure } from "../../components/Disclosure";
import { Tabs } from "../../components/Tabs";
import { Copy } from "../../copy/copy";
import { AutofillPanel } from "./AutofillPanel";
import { FitPanel } from "./FitPanel";
import { RiskPanel } from "./RiskPanel";
import { isHttpUrl, openExternalUrl } from "../../services/openExternal";
import { workerClient, WorkerError } from "../../services/workerClient";
import type {
  ApplicationsListResponseApplicationInfo,
  JobsGetDetailResponse,
} from "../../types/generated";

const STATUSES = [
  { value: "interested", label: "Interested" },
  { value: "applied", label: "Applied" },
  { value: "acknowledged", label: "Acknowledged" },
  { value: "recruiter_contacted", label: "Recruiter contacted" },
  { value: "interviewing", label: "Interviewing" },
  { value: "rejected", label: "Rejected" },
  { value: "withdrawn", label: "Withdrawn" },
  { value: "offered", label: "Offer" },
  { value: "accepted", label: "Accepted" },
];

const STATUS_LABELS: Record<string, string> = Object.fromEntries(
  STATUSES.map((status) => [status.value, status.label]),
);

function formatSalary(job: Record<string, unknown>): string | null {
  const min = typeof job.salary_min === "number" ? job.salary_min : null;
  const max = typeof job.salary_max === "number" ? job.salary_max : null;
  if (min === null && max === null) return null;
  const currency =
    typeof job.salary_currency === "string" && job.salary_currency
      ? `${job.salary_currency} `
      : "";
  const format = (value: number) => `${currency}${value.toLocaleString()}`;
  if (min !== null && max !== null) {
    return min === max ? format(min) : `${format(min)} – ${format(max)}`;
  }
  return min !== null ? `from ${format(min)}` : `up to ${format(max as number)}`;
}

function text(value: unknown, fallback = ""): string {
  return typeof value === "string" && value ? value : fallback;
}

const REPOST_CLASS_LABELS: Record<string, string> = {
  exact_duplicate: "Exact duplicate",
  reposted_identical: "Repost, same content",
  materially_updated: "Repost, material change",
  similar_distinct: "Similar but separate",
};

const RELATION_LABELS: Record<string, string> = {
  similar: "Similar role",
  duplicate: "Duplicate",
  repost: "Repost",
  user_merged: "Merged by you",
  user_unmerged: "Separated by you",
};

function formatTimestamp(value: unknown): string {
  const raw = text(value, "—");
  const parsed = new Date(raw);
  return raw === "—" || Number.isNaN(parsed.getTime()) ? raw : parsed.toLocaleString();
}

export function JobDetailRoute() {
  const { id } = useParams<{ id: string }>();
  const [detail, setDetail] = useState<JobsGetDetailResponse | null>(null);
  const [application, setApplication] =
    useState<ApplicationsListResponseApplicationInfo | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const [opening, setOpening] = useState(false);
  const [tab, setTab] = useState("job");
  const [autofillOpen, setAutofillOpen] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      if (!id) return;
      try {
        const [result, tracked, savedJobs] = await Promise.all([
          workerClient.getJobDetail({ job_id: id }),
          workerClient.listApplications(),
          workerClient.listSavedJobs(),
        ]);
        if (!cancelled) {
          setDetail(result);
          setApplication(tracked.applications.find((row) => row.job_id === id) ?? null);
          // One saved concept: the same job_user_state list Saved reads.
          setSaved(savedJobs.jobs.some((row) => row.job_id === id));
          setError(null);
        }
      } catch (exc) {
        if (!cancelled) {
          setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
        }
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [id]);

  const track = async (status: string) => {
    if (!id) return;
    setBusy(true);
    setError(null);
    try {
      if (application) {
        await workerClient.updateApplicationStatus({
          application_id: application.application_id,
          status,
        });
      } else {
        await workerClient.createApplication({ job_id: id, status });
      }
      const tracked = await workerClient.listApplications();
      setApplication(tracked.applications.find((row) => row.job_id === id) ?? null);
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const toggleSaved = async () => {
    if (!id || saved === null) return;
    setSaving(true);
    setError(null);
    try {
      await workerClient.setJobUserState({ job_id: id, state: saved ? "new" : "saved" });
      setSaved(!saved);
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setSaving(false);
    }
  };

  const job = detail?.job ?? {};
  const originalUrl = text(job.canonical_url);
  const description = text(job.description_text);
  const salary = formatSalary(job);
  const locationLine =
    [text(job.location_text), text(job.workplace_type)]
      .filter((value) => value && value !== "unknown")
      .join(" · ") || "Location not recorded";

  const openPosting = async () => {
    if (!isHttpUrl(originalUrl)) return;
    setOpening(true);
    setError(null);
    try {
      await openExternalUrl(originalUrl);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setOpening(false);
    }
  };

  const reposts = detail?.reposts ?? [];
  const relations = detail?.relations ?? [];
  const observations = detail?.observations ?? [];
  const snapshots = detail?.snapshots ?? [];
  const applied = Boolean(application);

  return (
    <div className="space-y-5">
      <Link to="/discover" className="inline-block text-sm text-slate-600 hover:underline">
        ← Back to matches
      </Link>

      <div className="sticky top-0 z-10 rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="text-xl font-semibold text-slate-900">
              {text(job.title, "Job detail")}
            </h1>
            <p className="mt-1 text-sm text-slate-700">
              {text(job.company_name, "Company not recorded")}
            </p>
            <p className="text-sm text-slate-600">{locationLine}</p>
            <p className="text-sm text-slate-600">
              {salary ? `Salary ${salary}` : "Salary not listed"}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant={saved ? "secondary" : "primary"}
              onClick={() => void toggleSaved()}
              disabled={saving || saved === null}
            >
              {saved === null ? "Loading saved state…" : saved ? "Saved ✓" : "Save for later"}
            </Button>
            {isHttpUrl(originalUrl) ? (
              <Button variant="primary" onClick={() => void openPosting()} disabled={opening}>
                {opening ? "Opening…" : "Open original"}
              </Button>
            ) : (
              <span className="text-xs text-slate-400">No original link recorded</span>
            )}
            <Button onClick={() => setAutofillOpen((open) => !open)} aria-expanded={autofillOpen}>
              Prepare application
            </Button>
          </div>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-slate-100 pt-3">
          {applied ? (
            <>
              <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-900">
                {STATUS_LABELS[application?.status ?? ""] ?? application?.status} ✓
              </span>
              <label className="flex items-center gap-2 text-xs text-slate-600">
                Update status
                <select
                  aria-label="Update application status"
                  value={application?.status ?? "applied"}
                  disabled={busy}
                  onChange={(event) => void track(event.target.value)}
                  className="rounded border border-slate-300 px-2 py-1 text-xs"
                >
                  {STATUSES.map((status) => (
                    <option key={status.value} value={status.value}>
                      {status.label}
                    </option>
                  ))}
                </select>
              </label>
              <Link to="/applications" className="text-xs text-slate-600 underline">
                Track it in Applications
              </Link>
            </>
          ) : saved ? (
            <Button variant="primary" disabled={busy} onClick={() => void track("applied")}>
              Mark as applied
            </Button>
          ) : (
            <span className="text-xs text-slate-500">
              Save this role to add it to your shortlist, then mark it applied when you have
              sent your application.
            </span>
          )}
        </div>
      </div>

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      ) : null}

      {autofillOpen && id ? (
        <div>
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-base font-semibold text-slate-900">Prepare application</h2>
            <p className="text-xs text-slate-500">
              You review every value. Nothing is submitted or clicked for you.
            </p>
          </div>
          <AutofillPanel jobId={id} />
        </div>
      ) : null}

      <Tabs
        label="Job sections"
        active={tab}
        onChange={setTab}
        items={[
          { id: "job", label: "Job" },
          { id: "why", label: "Why this job" },
          { id: "activity", label: "Activity" },
        ]}
      />

      {tab === "job" ? (
        <div className="space-y-4">
          <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
            <h2 className="text-base font-semibold text-slate-900">Description</h2>
            {description ? (
              <p className="mt-2 whitespace-pre-wrap text-sm leading-relaxed text-slate-700">
                {description}
              </p>
            ) : (
              <p className="mt-2 text-sm text-slate-500">
                No description was stored for this posting. Open the original to read it.
              </p>
            )}
          </section>

          <Disclosure summary="Posting facts" hint="Status, dates and observations.">
            <dl className="grid grid-cols-2 gap-3 text-sm text-slate-700 sm:grid-cols-4">
              <div>
                <dt className="text-xs text-slate-500">Posting status</dt>
                <dd>{text(job.posting_status, "unknown")}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">First seen</dt>
                <dd>{formatTimestamp(job.first_seen_at)}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Last seen</dt>
                <dd>{formatTimestamp(job.last_seen_at)}</dd>
              </div>
              <div>
                <dt className="text-xs text-slate-500">Times observed</dt>
                <dd>{observations.length}</dd>
              </div>
            </dl>
          </Disclosure>

          <Disclosure
            summary="Posting history"
            hint="Reposts, duplicates, snapshots and observations."
          >
            <div className="space-y-4 text-sm text-slate-700">
              <div>
                <h3 className="text-sm font-semibold text-slate-900">
                  Repost history
                  <span className="ml-1 text-xs font-normal text-slate-500">
                    {Copy.tooltips.reposts}
                  </span>
                </h3>
                {reposts.length === 0 ? (
                  <p className="mt-1 text-slate-500">No reposts detected.</p>
                ) : (
                  <ul className="mt-1 space-y-1">
                    {reposts.map((event, index) => (
                      <li key={index}>
                        {REPOST_CLASS_LABELS[text(event.class)] ?? text(event.class, "Repost")} ·
                        confidence{" "}
                        {typeof event.confidence === "number"
                          ? event.confidence.toFixed(2)
                          : "—"}
                        {event.material ? " · material change" : ""}
                        {event.posted_date_reset ? " · posted date reset" : ""}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h3 className="text-sm font-semibold text-slate-900">Duplicates & relations</h3>
                {relations.length === 0 ? (
                  <p className="mt-1 text-slate-500">No related jobs.</p>
                ) : (
                  <ul className="mt-1 space-y-1">
                    {relations.map((relation, index) => (
                      <li key={index}>
                        {RELATION_LABELS[text(relation.relation)] ??
                          text(relation.relation, "Related")}{" "}
                        · job {text(relation.related_job_id, text(relation.job_id, "—"))}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h3 className="text-sm font-semibold text-slate-900">
                  Snapshots ({snapshots.length})
                </h3>
                {snapshots.length === 0 ? (
                  <p className="mt-1 text-slate-500">No content snapshots recorded yet.</p>
                ) : (
                  <ul className="mt-1 space-y-1">
                    {snapshots.map((snapshot, index) => (
                      <li key={index}>
                        {formatTimestamp(snapshot.created_at)} · version{" "}
                        {text(snapshot.content_hash, "").slice(0, 12)}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div>
                <h3 className="text-sm font-semibold text-slate-900">
                  Observations ({observations.length})
                </h3>
                <p className="mt-1 text-slate-500">{Copy.tooltips.observations}</p>
              </div>
            </div>
          </Disclosure>
        </div>
      ) : null}

      {tab === "why" ? (
        <div className="space-y-4">
          {id ? (
            <FitPanel
              jobId={id}
              storedFit={typeof job.fit_score === "number" ? job.fit_score : null}
            />
          ) : null}
          {id ? <RiskPanel jobId={id} /> : null}
        </div>
      ) : null}

      {tab === "activity" ? (
        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-base font-semibold text-slate-900">Application activity</h2>
          {applied ? (
            <div className="mt-2 space-y-2 text-sm text-slate-700">
              <p>
                Current status:{" "}
                <span className="font-medium">
                  {STATUS_LABELS[application?.status ?? ""] ?? application?.status}
                </span>
              </p>
              <label className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                Change status
                <select
                  aria-label="Application status"
                  value={application?.status ?? "applied"}
                  disabled={busy}
                  onChange={(event) => void track(event.target.value)}
                  className="rounded border border-slate-300 px-2 py-1 text-xs"
                >
                  {STATUSES.map((status) => (
                    <option key={status.value} value={status.value}>
                      {status.label}
                    </option>
                  ))}
                </select>
              </label>
              <Link to="/applications" className={buttonClass("secondary")}>
                Open Applications
              </Link>
            </div>
          ) : (
            <div className="mt-2 text-sm text-slate-600">
              <p>No application recorded for this role yet.</p>
              <p className="mt-1 text-xs text-slate-500">
                Marking it applied records history only. It never submits anything. It hides
                similar roles at this company for the cooldown; saving never does.
              </p>
              <Button
                variant="primary"
                className="mt-2"
                disabled={busy}
                onClick={() => void track("applied")}
              >
                Mark as applied
              </Button>
            </div>
          )}
        </section>
      ) : null}
    </div>
  );
}
