import { useCallback, useEffect, useState } from "react";
import { InfoTip } from "../../components/InfoTip";
import { PageHeader } from "../../components/PageHeader";
import { Copy } from "../../copy/copy";
import { workerClient, WorkerError } from "../../services/workerClient";
import type {
  ApplicationsGetSuppressionsResponseSuppressionInfo,
  JobsListSeenResponseJobSummary,
} from "../../types/generated";

const reasonLabels: Record<string, string> = {
  duplicate: "Duplicate / repost",
  posting_removed: "No longer listed",
  suppressed: "Hidden after applying",
  suppression_expired: "Cooldown ended",
  suppression_restored: "Restored by you",
  dismissed: "Dismissed",
  hidden: "Hidden",
  saved: "Saved",
  new: "Marked new",
};

export function SeenRoute() {
  const [jobs, setJobs] = useState<JobsListSeenResponseJobSummary[]>([]);
  const [suppressions, setSuppressions] = useState<
    ApplicationsGetSuppressionsResponseSuppressionInfo[]
  >([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [result, suppressed] = await Promise.all([
        workerClient.listSeenJobs(),
        workerClient.getSuppressions(),
      ]);
      setJobs(result.jobs);
      setSuppressions(suppressed.suppressions);
      setError(null);
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

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
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const unmerge = async (jobId: string, relatedJobId: string) => {
    setBusy(true);
    setError(null);
    try {
      await workerClient.unmergeJobs({ job_id: jobId, related_job_id: relatedJobId });
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
        title="Previously seen"
        description="Postings that disappeared from their source, jobs linked as duplicates or reposts, and roles hidden because of applications."
      />

      {suppressions.length > 0 ? (
        <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
          <h2 className="text-lg font-semibold text-slate-900">Hidden roles</h2>
          <p className="mt-1 text-xs text-slate-500">
            Why each role is out of your results, and when the cooldown ends.
          </p>
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
                  <button
                    type="button"
                    disabled={busy || row.state === "restored"}
                    onClick={() => void restore(row.job_id, row.application_id)}
                    className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  >
                    {row.state === "restored" ? "Restored" : "Restore"}
                  </button>
                </div>
                <p className="mt-1 text-slate-600">{row.explanation}</p>
                <div className="mt-1 text-xs text-slate-500">
                  {reasonLabels[row.reason] ?? row.reason}
                  {row.expires_at ? ` · until ${new Date(row.expires_at).toLocaleDateString()}` : ""}
                </div>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        {jobs.length === 0 ? (
          <p className="text-sm text-slate-500">
            Nothing here yet. Postings appear when they are removed from their source or
            linked as duplicates or reposts.
          </p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {jobs.map((job) => {
              const reason = job.reason ?? "";
              return (
                <li key={job.job_id} className="py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-slate-900">{job.title}</span>
                    <span
                      title={Copy.tooltips.seen}
                      className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700"
                    >
                      {reasonLabels[reason] ?? job.posting_status ?? "Seen"}
                    </span>
                  </div>
                  {job.canonical_url ? (
                    <div className="mt-0.5 truncate text-xs text-slate-500">{job.canonical_url}</div>
                  ) : null}
                  {job.related_job_ids && job.related_job_ids.length > 0 ? (
                    <div className="mt-2 flex flex-wrap items-center gap-2">
                      <span className="text-xs text-slate-500">
                        Linked to job {job.related_job_ids.join(", ")}
                        <InfoTip label="Linked jobs">{Copy.tooltips.relatedJob}</InfoTip>
                      </span>
                      {job.related_job_ids.map((relatedId) => (
                        <button
                          key={relatedId}
                          type="button"
                          disabled={busy}
                          onClick={() => void unmerge(job.job_id, relatedId)}
                          title={Copy.tooltips.unmerge}
                          className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                        >
                          Keep separate
                        </button>
                      ))}
                    </div>
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
