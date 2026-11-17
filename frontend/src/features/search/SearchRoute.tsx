import { useCallback, useEffect, useState } from "react";
import { PageHeader } from "../../components/PageHeader";
import { workerClient, WorkerError } from "../../services/workerClient";
import type {
  JobsListResponseJobSummary,
  SearchProfilesListResponseSearchProfileInfo,
} from "../../types/generated";

export function SearchRoute() {
  const [profiles, setProfiles] = useState<SearchProfilesListResponseSearchProfileInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [jobs, setJobs] = useState<JobsListResponseJobSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadProfiles = useCallback(async () => {
    try {
      const result = await workerClient.listSearchProfiles();
      setProfiles(result.profiles);
      if (result.profiles.length > 0 && selected === null) {
        setSelected(result.profiles[0].profile_id);
      }
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    }
  }, [selected]);

  useEffect(() => {
    void loadProfiles();
  }, [loadProfiles]);

  const runSearch = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const result = await workerClient.listJobs({ query: query || undefined, limit: 50 });
      setJobs(result.jobs);
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  }, [query]);

  useEffect(() => {
    void runSearch();
  }, [runSearch]);

  const saveProfile = async (name: string) => {
    setBusy(true);
    try {
      await workerClient.saveSearchProfile({ name, hard_filters: {} });
      await loadProfiles();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const deleteProfile = async (profileId: string) => {
    setBusy(true);
    try {
      await workerClient.deleteSearchProfile({ profile_id: profileId });
      setSelected(null);
      await loadProfiles();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader title="Search" description="Search jobs with full-text search and filters." />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Search profiles</h2>
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              const name = window.prompt("Profile name");
              if (name) void saveProfile(name);
            }}
            className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            New profile
          </button>
          <select
            className="rounded border border-slate-300 px-3 py-2 text-sm"
            value={selected ?? ""}
            onChange={(e) => setSelected(e.target.value || null)}
          >
            <option value="">— select —</option>
            {profiles.map((p) => (
              <option key={p.profile_id} value={p.profile_id}>
                {p.name}
              </option>
            ))}
          </select>
          {selected ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => void deleteProfile(selected)}
              className="rounded border border-red-300 px-2 py-1 text-xs font-medium text-red-700 hover:bg-red-50 disabled:opacity-50"
            >
              Delete
            </button>
          ) : null}
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Search</h2>
        <div className="mt-3 flex gap-2">
          <input
            className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm"
            placeholder="Search jobs (full-text)..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button
            type="button"
            onClick={() => void runSearch()}
            disabled={busy}
            className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {busy ? "Searching..." : "Search"}
          </button>
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Results ({jobs.length})</h2>
        {jobs.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No jobs match.</p>
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
