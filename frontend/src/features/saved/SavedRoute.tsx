import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { PageHeader } from "../../components/PageHeader";
import { RiskBadge } from "../../components/RiskBadge";
import { workerClient, WorkerError } from "../../services/workerClient";
import type { JobsListSavedResponseJobSummary } from "../../types/generated";

export function SavedRoute() {
  const [jobs, setJobs] = useState<JobsListSavedResponseJobSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async () => {
    try {
      const result = await workerClient.listSavedJobs();
      setJobs(result.jobs);
      setError(null);
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const unsave = async (jobId: string) => {
    setBusy(true);
    try {
      await workerClient.setJobUserState({ job_id: jobId, state: "new" });
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
        title="Saved jobs"
        description="Roles you saved for later. Saving never hides other jobs."
      />
      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      ) : null}
      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        {!loaded ? (
          <p className="text-sm text-slate-500">Loading saved jobs…</p>
        ) : jobs.length === 0 ? (
          <p className="text-sm text-slate-500">
            Nothing saved yet. Open a job from Discover and choose Save.
          </p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {jobs.map((job) => (
              <li key={job.job_id} className="flex flex-wrap items-center gap-3 py-2">
                <div className="min-w-0 flex-1">
                  <Link className="font-medium text-slate-900 underline" to={`/jobs/${job.job_id}`}>
                    {job.title}
                  </Link>
                  <div className="text-xs text-slate-500">
                    {[job.company_name, job.location_text, job.posting_status]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-2 text-xs text-slate-500">
                    <span>
                      Fit {typeof job.fit_score === "number" ? job.fit_score.toFixed(2) : "—"}
                    </span>
                    <span>
                      Credibility{" "}
                      {typeof job.credibility === "number" ? job.credibility.toFixed(2) : "—"}
                    </span>
                    <RiskBadge
                      riskClass={job.risk_class}
                      confidence={job.risk_confidence}
                      label={job.risk_label}
                    />
                  </div>
                </div>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void unsave(job.job_id)}
                  className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  Remove from saved
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
