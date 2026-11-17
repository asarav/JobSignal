import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { workerClient, WorkerError } from "../../services/workerClient";
import type { JobsListResponseJobSummary, SourcesListResponseSourceInfo } from "../../types/generated";

export function DiscoverRoute() {
  const [jobs, setJobs] = useState<JobsListResponseJobSummary[]>([]);
  const [sources, setSources] = useState<SourcesListResponseSourceInfo[]>([]);
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

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
      await workerClient.addSource({ url: url.trim() });
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
    try {
      await workerClient.runDiscovery({ source_id: sourceId });
      await load();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Discover jobs" description="Add a job source and run discovery." />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Sources</h2>
        <div className="mt-3 flex gap-2">
          <input
            className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm"
            placeholder="https://boards.greenhouse.io/your-company"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <button
            type="button"
            onClick={() => void addSource()}
            disabled={busy}
            className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            Add source
          </button>
        </div>
        <ul className="mt-3 space-y-2">
          {sources.map((source) => (
            <li key={source.source_id} className="flex items-center justify-between rounded border border-slate-200 px-3 py-2 text-sm">
              <span className="truncate">{source.url}</span>
              <button
                type="button"
                onClick={() => void runDiscovery(source.source_id)}
                disabled={busy}
                className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
              >
                Run
              </button>
            </li>
          ))}
          {sources.length === 0 ? <li className="text-sm text-slate-500">No sources yet.</li> : null}
        </ul>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Jobs ({jobs.length})</h2>
        {jobs.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No jobs discovered yet.</p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-100">
            {jobs.map((job) => (
              <li key={job.job_id} className="py-2">
                <div className="font-medium text-slate-900">{job.title}</div>
                <div className="text-xs text-slate-500">{job.canonical_url}</div>
                {job.provenance && job.provenance.length > 0 ? (
                  <div className="mt-1 text-xs text-slate-400">
                    extracted by: {job.provenance.join(" → ")}
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
