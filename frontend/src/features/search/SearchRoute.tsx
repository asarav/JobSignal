import { useCallback, useEffect, useState } from "react";
import { InfoTip } from "../../components/InfoTip";
import { PageHeader } from "../../components/PageHeader";
import { Copy } from "../../copy/copy";
import { workerClient, WorkerError } from "../../services/workerClient";
import type {
  JobsListResponseJobSummary,
  SearchProfilesListResponseSearchProfileInfo,
} from "../../types/generated";

export function SearchRoute() {
  const [profiles, setProfiles] = useState<SearchProfilesListResponseSearchProfileInfo[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [titleInclude, setTitleInclude] = useState("");
  const [location, setLocation] = useState("");
  const [workplace, setWorkplace] = useState("");
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

  const selectedProfile =
    profiles.find((profile) => profile.profile_id === selected) ?? null;
  // Stable dependency: the profile object is rebuilt on every load.
  const profileFilters = JSON.stringify(selectedProfile?.hard_filters ?? {});

  // Show the selected profile's filters in the editor.
  useEffect(() => {
    const filters = selectedProfile?.hard_filters ?? {};
    const title = filters.title_include;
    const place = filters.location;
    const types = filters.workplace_types;
    setTitleInclude(typeof title === "string" ? title : "");
    setLocation(typeof place === "string" ? place : "");
    setWorkplace(
      Array.isArray(types) && typeof types[0] === "string" ? types[0] : "",
    );
  }, [selectedProfile]);

  const runSearch = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const filters = JSON.parse(profileFilters) as Record<string, unknown>;
      const result = await workerClient.listJobs({
        query: query || undefined,
        filters,
        limit: 50,
      });
      setJobs(result.jobs);
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  }, [query, profileFilters]);

  useEffect(() => {
    void runSearch();
  }, [runSearch]);

  const currentFilters = (): Record<string, unknown> => {
    const filters: Record<string, unknown> = {};
    if (titleInclude.trim()) filters.title_include = titleInclude.trim();
    if (location.trim()) filters.location = location.trim();
    if (workplace) filters.workplace_types = [workplace];
    return filters;
  };

  const saveProfile = async (name: string) => {
    setBusy(true);
    try {
      const saved = await workerClient.saveSearchProfile({
        profile_id: selectedProfile?.profile_id ?? undefined,
        name,
        hard_filters: currentFilters(),
      });
      setSelected(saved.profile_id);
      await loadProfiles();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const saveFilters = async () => {
    if (!selectedProfile) return;
    await saveProfile(selectedProfile.name);
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
      <PageHeader
        title="Search jobs"
        description="Search your local job database by text, and narrow results with the selected profile's saved hard filters."
      />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>
      ) : null}

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900" aria-label="Search profiles">
          Search profiles
          <InfoTip label="Search profiles">{Copy.tooltips.profile}</InfoTip>
        </h2>
        <div className="mt-3 flex gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => {
              const name = window.prompt("Name this search profile");
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
            <option value="">— choose a profile —</option>
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
              Delete profile
            </button>
          ) : null}
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900" aria-label="Hard filters">
          Hard filters
          <InfoTip label="Hard filters">{Copy.tooltips.hardFilters}</InfoTip>
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          Saved filters narrow the results below and rank discovery sources into hot,
          warm and cold tiers. Save edits before searching.
        </p>
        <div className="mt-3 grid gap-2 sm:grid-cols-3">
          <input
            className="rounded border border-slate-300 px-3 py-2 text-sm"
            placeholder="Title contains (e.g. engineer)"
            aria-label="Title contains"
            value={titleInclude}
            onChange={(e) => setTitleInclude(e.target.value)}
          />
          <input
            className="rounded border border-slate-300 px-3 py-2 text-sm"
            placeholder="Location contains (e.g. Berlin)"
            aria-label="Location contains"
            value={location}
            onChange={(e) => setLocation(e.target.value)}
          />
          <select
            className="rounded border border-slate-300 px-3 py-2 text-sm"
            value={workplace}
            onChange={(e) => setWorkplace(e.target.value)}
          >
            <option value="">Any workplace</option>
            <option value="remote">Remote</option>
            <option value="hybrid">Hybrid</option>
            <option value="onsite">On-site</option>
          </select>
        </div>
        {selectedProfile ? (
          <button
            type="button"
            disabled={busy}
            onClick={() => void saveFilters()}
            title={Copy.tooltips.saveFilters}
            className="mt-3 rounded border border-slate-300 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Save filters to “{selectedProfile.name}”
          </button>
        ) : null}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Keyword search</h2>
        <div className="mt-3 flex gap-2">
          <input
            className="flex-1 rounded border border-slate-300 px-3 py-2 text-sm"
            placeholder="Search job titles and descriptions"
            aria-label="Search job titles and descriptions"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <button
            type="button"
            onClick={() => void runSearch()}
            disabled={busy}
            className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {busy ? "Searching…" : "Search"}
          </button>
        </div>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2
          className="text-lg font-semibold text-slate-900"
          aria-label={`Results (${jobs.length})`}
        >
          Results ({jobs.length})
          <InfoTip label="Results">{Copy.tooltips.results}</InfoTip>
        </h2>
        {jobs.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">
            No jobs match these filters. Clear a filter or add sources on Discover Jobs.
          </p>
        ) : (
          <ul className="mt-3 divide-y divide-slate-100">
            {jobs.map((job) => (
              <li key={job.job_id} className="py-2">
                <div className="font-medium text-slate-900">{job.title}</div>
                <div className="text-xs text-slate-500">{job.canonical_url}</div>
                {job.provenance && job.provenance.length > 0 ? (
                  <div className="mt-1 text-xs text-slate-400">
                    <span title={Copy.tooltips.extractedBy}>
                      Extracted by: {job.provenance.join(" → ")}
                    </span>
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
