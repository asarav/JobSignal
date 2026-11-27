import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Button } from "../../components/Button";
import { CompanyAvatar } from "../../components/CompanyAvatar";
import { Disclosure } from "../../components/Disclosure";
import { EmptyState } from "../../components/EmptyState";
import { FocusQueue } from "../../components/FocusQueue";
import { PageHeader } from "../../components/PageHeader";
import { Pagination } from "../../components/Pagination";
import { RiskBadge } from "../../components/RiskBadge";
import { ScoreMeter } from "../../components/ScoreMeter";
import { Tabs } from "../../components/Tabs";
import { useToast } from "../../components/Toast";
import { useLiveRefresh } from "../../hooks/useLiveRefresh";
import { WorkflowNextStepBar } from "../../components/WorkflowNextStepBar";
import { workerClient, WorkerError } from "../../services/workerClient";
import { CollectJobsAction } from "../workflow/CollectJobsAction";
import { DiscoveryStatus } from "./DiscoveryStatus";
import { useWorkflowState } from "../workflow/useWorkflowState";
import { lastVisitCutoff, readLastVisit } from "../workflow/lastVisit";
import type { SearchProfilesListResponseSearchProfileInfo } from "../../types/generated";

interface JobRow {
  job_id: string;
  title: string;
  company_name?: string;
  location_text?: string;
  workplace_type?: string;
  posting_status?: string;
  canonical_url?: string | null;
  salary_min?: number | null;
  salary_max?: number | null;
  salary_currency?: string | null;
  fit_score?: number | null;
  credibility?: number | null;
  risk_class?: string | null;
  risk_confidence?: number | null;
  risk_label?: string | null;
  rank_score?: number | null;
  rank_reasons?: string[];
  saved?: boolean;
  hidden_by_policy?: boolean;
  first_seen_at?: string | null;
}

type Preset = "all" | "new" | "fit" | "verify" | "remote";

const POLICIES = [
  { value: "deprioritize", label: "Move low-credibility jobs down" },
  { value: "show_all", label: "Show everything" },
  { value: "hide_high_risk", label: "Hide likely-inactive jobs" },
  { value: "require_evidence", label: "Require positive evidence" },
];

const SORTS = [
  { value: "rank", label: "Best match" },
  { value: "fit", label: "Fit only" },
  { value: "freshness", label: "Newest" },
  { value: "credibility", label: "Credibility" },
] as const;

const PRESETS: { id: Preset; label: string }[] = [
  { id: "new", label: "New since last visit" },
  { id: "fit", label: "Strong fit" },
  { id: "verify", label: "Needs verification" },
  { id: "remote", label: "Remote" },
  { id: "all", label: "All" },
];

function message(exc: unknown): string {
  return exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc);
}

function salaryLabel(job: JobRow): string | null {
  const min = typeof job.salary_min === "number" ? job.salary_min : null;
  const max = typeof job.salary_max === "number" ? job.salary_max : null;
  if (min === null && max === null) return null;
  const currency = job.salary_currency ? `${job.salary_currency} ` : "";
  const format = (value: number) => `${currency}${value.toLocaleString()}`;
  if (min !== null && max !== null) {
    return min === max ? format(min) : `${format(min)} – ${format(max)}`;
  }
  return min !== null ? `from ${format(min)}` : `up to ${format(max as number)}`;
}

function ageLabel(value?: string | null): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  const days = Math.max(0, Math.floor((Date.now() - parsed.getTime()) / 86_400_000));
  return days === 0 ? "new today" : `${days}d old`;
}

function filtersFrom(
  title: string,
  location: string,
  workplace: string,
  preset: Preset,
): Record<string, unknown> {
  const filters: Record<string, unknown> = {};
  if (title.trim()) filters.title_include = title.trim();
  if (location.trim()) filters.location = location.trim();
  const type = preset === "remote" ? "remote" : workplace;
  if (type) filters.workplace_types = [type];
  if (preset === "new") {
    filters.first_seen_after = lastVisitCutoff(Date.now(), readLastVisit());
  }
  return filters;
}

export function DiscoverRoute() {
  const workflow = useWorkflowState();
  const toast = useToast();
  const [params, setParams] = useSearchParams();
  const initialPreset = (params.get("new") === "1"
    ? "new"
    : params.get("preset") ?? "all") as Preset;
  const [view, setView] = useState(params.get("view") === "search" ? "search" : "best");
  const [preset, setPreset] = useState<Preset>(PRESETS.some((item) => item.id === initialPreset) ? initialPreset : "all");
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [title, setTitle] = useState(params.get("title") ?? "");
  const [location, setLocation] = useState(params.get("location") ?? "");
  const [workplace, setWorkplace] = useState(params.get("workplace") ?? "");
  const [policy, setPolicy] = useState(params.get("policy") ?? "deprioritize");
  const [sort, setSort] = useState<(typeof SORTS)[number]["value"]>(
    (params.get("sort") as (typeof SORTS)[number]["value"]) || "rank",
  );
  const [includeHidden, setIncludeHidden] = useState(params.get("hidden") === "1");
  const [includeOutsideArea, setIncludeOutsideArea] = useState(
    params.get("outside") === "1",
  );
  const [includeMissingSkills, setIncludeMissingSkills] = useState(
    params.get("missing") === "1",
  );
  const [outsideArea, setOutsideArea] = useState(0);
  const [hiddenMissingSkills, setHiddenMissingSkills] = useState(0);
  const [filtersOpen, setFiltersOpen] = useState(params.get("filters") === "1");
  const [pageSize, setPageSize] = useState(25);
  const [jobs, setJobs] = useState<JobRow[]>([]);
  const [total, setTotal] = useState(0);
  const [hiddenCount, setHiddenCount] = useState(0);
  const [profiles, setProfiles] = useState<SearchProfilesListResponseSearchProfileInfo[]>([]);
  const [selectedProfile, setSelectedProfile] = useState(params.get("profile") ?? "");
  const [profileName, setProfileName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [savingId, setSavingId] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [focusOpen, setFocusOpen] = useState(false);
  const [flashId, setFlashId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    void workerClient
      .listSearchProfiles()
      .then((result) => {
        if (!cancelled) setProfiles(result.profiles);
      })
      .catch(() => {
        if (!cancelled) setProfiles([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const next = new URLSearchParams();
    if (view === "search") next.set("view", "search");
    if (query) next.set("q", query);
    if (title) next.set("title", title);
    if (location) next.set("location", location);
    if (workplace) next.set("workplace", workplace);
    if (policy !== "deprioritize") next.set("policy", policy);
    if (sort !== "rank") next.set("sort", sort);
    if (includeHidden) next.set("hidden", "1");
    if (includeOutsideArea) next.set("outside", "1");
    if (includeMissingSkills) next.set("missing", "1");
    if (filtersOpen) next.set("filters", "1");
    if (selectedProfile) next.set("profile", selectedProfile);
    if (preset === "new") next.set("new", "1");
    else if (preset !== "all") next.set("preset", preset);
    setParams(next, { replace: true });
  }, [
    view,
    preset,
    query,
    title,
    location,
    workplace,
    policy,
    sort,
    includeHidden,
    includeOutsideArea,
    includeMissingSkills,
    filtersOpen,
    selectedProfile,
    setParams,
  ]);

  useEffect(() => {
    const urlView = params.get("view") === "search" ? "search" : "best";
    const urlPreset = (params.get("new") === "1"
      ? "new"
      : params.get("preset") ?? "all") as Preset;
    setView(urlView);
    setPreset(PRESETS.some((item) => item.id === urlPreset) ? urlPreset : "all");
    setQuery(params.get("q") ?? "");
    setTitle(params.get("title") ?? "");
    setLocation(params.get("location") ?? "");
    setWorkplace(params.get("workplace") ?? "");
    setPolicy(params.get("policy") ?? "deprioritize");
    setSort((params.get("sort") as (typeof SORTS)[number]["value"]) || "rank");
    setIncludeHidden(params.get("hidden") === "1");
    setIncludeOutsideArea(params.get("outside") === "1");
    setIncludeMissingSkills(params.get("missing") === "1");
    setFiltersOpen(params.get("filters") === "1");
    setSelectedProfile(params.get("profile") ?? "");
  }, [params]);

  const load = useCallback(
    async (offset = 0, append = false) => {
      setBusy(true);
      try {
        const filters = filtersFrom(title, location, workplace, preset);
        if (view === "best") {
          const result = await workerClient.rankedJobs({
            query: query.trim() || undefined,
            filters,
            ranking_policy: policy,
            sort: preset === "fit" ? "fit" : sort,
            include_hidden: includeHidden,
            include_suppressed: includeHidden,
            include_out_of_area: includeOutsideArea,
            include_missing_skills: includeMissingSkills,
            ensure_fit: true,
            search_profile_id: selectedProfile || undefined,
            min_fit: preset === "fit" ? 0.75 : undefined,
            risk_classes:
              preset === "verify" ? ["needs_verification", "likely_inactive"] : undefined,
            limit: pageSize,
            offset,
          });
          const rows = (result.jobs ?? []) as JobRow[];
          setJobs((current) => (append ? [...current, ...rows] : rows));
          setTotal(result.total ?? rows.length);
          setHiddenCount(result.hidden_by_policy ?? 0);
          setOutsideArea(result.outside_area ?? 0);
          setHiddenMissingSkills(result.hidden_missing_skills ?? 0);
        } else {
          const result = await workerClient.listJobs({
            query: query.trim() || undefined,
            filters,
            limit: pageSize,
            offset,
          });
          const rows = result.jobs as JobRow[];
          setJobs((current) => (append ? [...current, ...rows] : rows));
          setTotal(result.total ?? rows.length);
          setHiddenCount(0);
          setOutsideArea(0);
          setHiddenMissingSkills(0);
        }
        setError(null);
      } catch (exc) {
        setError(message(exc));
      } finally {
        setBusy(false);
        setLoaded(true);
      }
    },
    [
      view,
      query,
      title,
      location,
      workplace,
      policy,
      sort,
      includeHidden,
      includeOutsideArea,
      includeMissingSkills,
      selectedProfile,
      preset,
      pageSize,
    ],
  );

  useEffect(() => {
    void load(0, false);
  }, [load]);
  useLiveRefresh(() => void load(0, false));

  const applyProfile = (profileId: string) => {
    setSelectedProfile(profileId);
    setProfileName("");
    const profile = profiles.find((row) => row.profile_id === profileId);
    if (!profile) return;
    const saved = profile.hard_filters ?? {};
    setTitle(typeof saved.title_include === "string" ? saved.title_include : "");
    setLocation(typeof saved.location === "string" ? saved.location : "");
    const types = saved.workplace_types;
    setWorkplace(Array.isArray(types) && typeof types[0] === "string" ? types[0] : "");
    setPolicy(profile.ranking_policy ?? "deprioritize");
    setPreset("all");
  };

  const saveSearch = async () => {
    const existing = profiles.find((row) => row.profile_id === selectedProfile);
    const name = profileName.trim() || existing?.name || "";
    if (!name) return;
    setBusy(true);
    try {
      const saved = await workerClient.saveSearchProfile({
        profile_id: selectedProfile || undefined,
        name,
        hard_filters: filtersFrom(title, location, workplace, preset),
        ranking_policy: policy,
      });
      const refreshed = await workerClient.listSearchProfiles();
      setProfiles(refreshed.profiles);
      setSelectedProfile(saved.profile_id);
      setProfileName("");
      setError(null);
    } catch (exc) {
      setError(message(exc));
    } finally {
      setBusy(false);
    }
  };

  const deleteSearch = async () => {
    if (!selectedProfile) return;
    setBusy(true);
    try {
      await workerClient.deleteSearchProfile({ profile_id: selectedProfile });
      const refreshed = await workerClient.listSearchProfiles();
      setProfiles(refreshed.profiles);
      setSelectedProfile("");
      setError(null);
    } catch (exc) {
      setError(message(exc));
    } finally {
      setBusy(false);
    }
  };

  const setSaved = async (job: JobRow, saved: boolean): Promise<boolean> => {
    setSavingId(job.job_id);
    setJobs((current) =>
      current.map((row) => (row.job_id === job.job_id ? { ...row, saved } : row)),
    );
    try {
      await workerClient.setJobUserState({
        job_id: job.job_id,
        state: saved ? "saved" : "new",
      });
      toast.push(saved ? `Saved “${job.title}”.` : `Removed “${job.title}” from saved.`, {
        tone: saved ? "success" : "info",
        action: saved ? { label: "Open apply queue", to: "/saved?view=ready" } : undefined,
      });
      if (saved) {
        setFlashId(job.job_id);
        window.setTimeout(() => {
          setFlashId((current) => (current === job.job_id ? null : current));
        }, 900);
      }
      workflow.refresh();
      setError(null);
      return true;
    } catch (exc) {
      setJobs((current) =>
        current.map((row) => (row.job_id === job.job_id ? { ...row, saved: !saved } : row)),
      );
      setError(message(exc));
      toast.push("Could not update saved state. Reverted.", { tone: "danger" });
      return false;
    } finally {
      setSavingId(null);
    }
  };

  const toggleSaved = (job: JobRow) => setSaved(job, !job.saved);

  const dismiss = async (job: JobRow): Promise<boolean> => {
    try {
      await workerClient.setJobUserState({ job_id: job.job_id, state: "dismissed" });
      toast.push(`Dismissed “${job.title}”.`, { tone: "info" });
      workflow.refresh();
      return true;
    } catch (exc) {
      setError(message(exc));
      toast.push("Could not dismiss this job.", { tone: "danger" });
      return false;
    }
  };

  const clearFilters = () => {
    setQuery("");
    setTitle("");
    setLocation("");
    setWorkplace("");
    setSelectedProfile("");
    setPreset("all");
  };

  const selectPreset = (next: Preset) => {
    if (next === "fit" || next === "verify") setView("best");
    setPreset(next);
    setFocusOpen(false);
  };

  const filtered = Boolean(query || title || location || workplace || preset !== "all");
  const focusTotal = total;

  return (
    <div className="space-y-5">
      <PageHeader
        title="Find jobs"
        description="Best matches first, or search everything already collected. Saving adds a role to your shortlist."
        actions={
          jobs.length > 0 ? (
            <Button variant="primary" onClick={() => setFocusOpen(true)}>
              Focus review
            </Button>
          ) : undefined
        }
      />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">
          {error}
        </div>
      ) : null}

      <DiscoveryStatus onJobsChanged={() => void load(0, false)} />

      <div className="rounded-lg border border-line bg-white p-3 shadow-card">
        <div className="flex flex-wrap items-center gap-2">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search titles, companies, description…"
            aria-label="Search jobs"
            className="min-w-[12rem] flex-1 rounded-md border border-line px-3 py-2 text-sm focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
          />
          <select
            aria-label="Saved search"
            value={selectedProfile}
            onChange={(event) => applyProfile(event.target.value)}
            className="rounded-md border border-line px-2 py-2 text-sm"
          >
            <option value="">Saved searches</option>
            {profiles.map((profile) => (
              <option key={profile.profile_id} value={profile.profile_id}>
                {profile.name}
              </option>
            ))}
          </select>
          <Button aria-expanded={filtersOpen} onClick={() => setFiltersOpen((open) => !open)}>
            Filters
          </Button>
          {view === "best" ? (
            <select
              aria-label="Sort order"
              value={sort}
              onChange={(event) => setSort(event.target.value as typeof sort)}
              className="rounded-md border border-line px-2 py-2 text-sm"
            >
              {SORTS.map((entry) => (
                <option key={entry.value} value={entry.value}>
                  {entry.label}
                </option>
              ))}
            </select>
          ) : null}
          <select
            aria-label="Results per page"
            value={pageSize}
            onChange={(event) => setPageSize(Number(event.target.value))}
            className="rounded-md border border-line px-2 py-2 text-sm"
          >
            <option value={25}>25 / page</option>
            <option value={50}>50 / page</option>
          </select>
        </div>

        <div className="mt-3 flex flex-wrap gap-1.5 border-t border-line pt-3" aria-label="Quick presets">
          {PRESETS.map((entry) => (
            <button
              key={entry.id}
              type="button"
              aria-pressed={preset === entry.id}
              onClick={() => selectPreset(entry.id)}
              className={`rounded-full border px-2.5 py-1 text-xs font-medium transition ${
                preset === entry.id
                  ? "border-brand-600 bg-brand-600 text-white"
                  : "border-line bg-white text-slate-600 hover:border-brand-200 hover:text-brand-700"
              }`}
            >
              {entry.label}
            </button>
          ))}
          {preset === "new" ? (
            <span className="self-center text-[11px] text-slate-500">
              Using your last visit timestamp.
            </span>
          ) : null}
        </div>

        {filtersOpen ? (
          <div className="mt-3 grid gap-3 border-t border-line pt-3 sm:grid-cols-2 lg:grid-cols-4">
            <label className="flex flex-col gap-1 text-xs text-slate-500">
              Title contains
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                className="rounded-md border border-line px-2 py-1.5 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-slate-500">
              Location
              <input
                value={location}
                onChange={(event) => setLocation(event.target.value)}
                className="rounded-md border border-line px-2 py-1.5 text-sm"
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-slate-500">
              Workplace
              <select
                value={workplace}
                onChange={(event) => {
                  setWorkplace(event.target.value);
                  if (preset === "remote") setPreset("all");
                }}
                className="rounded-md border border-line px-2 py-1.5 text-sm"
              >
                <option value="">Any</option>
                <option value="remote">Remote</option>
                <option value="hybrid">Hybrid</option>
                <option value="onsite">On-site</option>
              </select>
            </label>
            {view === "best" ? (
              <label className="flex flex-col gap-1 text-xs text-slate-500">
                Credibility policy
                <select
                  value={policy}
                  onChange={(event) => setPolicy(event.target.value)}
                  className="rounded-md border border-line px-2 py-1.5 text-sm"
                >
                  {POLICIES.map((entry) => (
                    <option key={entry.value} value={entry.value}>
                      {entry.label}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <label className="flex items-center gap-2 text-xs text-slate-600">
              <input
                type="checkbox"
                checked={includeHidden}
                onChange={(event) => setIncludeHidden(event.target.checked)}
              />
              Include hidden and suppressed
            </label>
            {view === "best" ? (
              <label className="flex items-center gap-2 text-xs text-slate-600">
                <input
                  type="checkbox"
                  checked={!includeOutsideArea}
                  onChange={(event) => setIncludeOutsideArea(!event.target.checked)}
                />
                Only my area (from my preferences)
              </label>
            ) : null}
            {view === "best" ? (
              <label className="flex items-center gap-2 text-xs text-slate-600">
                <input
                  type="checkbox"
                  checked={!includeMissingSkills}
                  onChange={(event) => setIncludeMissingSkills(!event.target.checked)}
                />
                Only roles matching required skills
              </label>
            ) : null}
            <div className="flex items-end gap-2 sm:col-span-2">
              <input
                value={profileName}
                onChange={(event) => setProfileName(event.target.value)}
                placeholder={
                  profiles.find((row) => row.profile_id === selectedProfile)?.name ??
                  "Name this search"
                }
                aria-label="Saved search name"
                className="min-w-0 flex-1 rounded-md border border-line px-2 py-1.5 text-sm"
              />
              <Button disabled={busy} onClick={() => void saveSearch()}>
                {selectedProfile ? "Update search" : "Save search"}
              </Button>
              {selectedProfile ? (
                <Button variant="quiet" disabled={busy} onClick={() => void deleteSearch()}>
                  Delete
                </Button>
              ) : null}
            </div>
          </div>
        ) : null}
      </div>

      <Tabs
        label="Job results"
        active={view}
        onChange={setView}
        items={[
          { id: "best", label: "Best matches" },
          { id: "search", label: "Search all" },
        ]}
      />

      {focusOpen ? (
        <FocusQueue
          jobs={jobs}
          total={focusTotal}
          busy={savingId !== null}
          onClose={() => {
            setFocusOpen(false);
            void load(0, false);
          }}
          onSave={async (job) => (job.saved ? true : setSaved(job, true))}
          onDismiss={dismiss}
          onLoadMore={() => void load(jobs.length, true)}
        />
      ) : (
        <section className="rounded-lg border border-line bg-white p-4 shadow-card">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-base font-semibold text-slate-900">
              {view === "best" ? "Ranked matches" : "All matching jobs"}{" "}
              <span className="tabular font-normal text-slate-500">({total})</span>
            </h2>
            <p className="text-xs text-slate-500">
              {view === "best"
                ? "Fit and credibility decide the order."
                : "Text and filters only; no ranking."}
              {view === "best" && hiddenCount > 0
                ? ` ${hiddenCount} hidden by the selected policy.`
                : ""}
              {view === "best" && outsideArea > 0 && !includeOutsideArea
                ? ` ${outsideArea} outside your area hidden.`
                : ""}
              {view === "best" &&
              hiddenMissingSkills > 0 &&
              !includeMissingSkills
                ? ` ${hiddenMissingSkills} hidden for missing required skills.`
                : ""}
              {view === "best" && outsideArea > 0 && !includeOutsideArea ? (
                <button
                  type="button"
                  className="ml-1 font-medium text-brand-700 underline"
                  onClick={() => setIncludeOutsideArea(true)}
                >
                  Show them
                </button>
              ) : null}{" "}
              {view === "best" &&
              hiddenMissingSkills > 0 &&
              !includeMissingSkills ? (
                <button
                  type="button"
                  className="ml-1 font-medium text-brand-700 underline"
                  onClick={() => setIncludeMissingSkills(true)}
                >
                  Show them
                </button>
              ) : null}
            </p>
          </div>

          {!loaded ? (
            <p className="mt-3 text-sm text-slate-500">Loading jobs…</p>
          ) : jobs.length === 0 ? (
            <div className="mt-3">
              <EmptyState
                icon="search"
                tone={filtered ? "amber" : "brand"}
                heading={filtered ? "No jobs match these filters" : "No matches yet"}
                body={
                  filtered
                    ? "Widen the search or clear the filters to see more of what has been collected."
                    : "Add a company careers page or job board in Settings, then crawl it once. Matches appear here as postings arrive."
                }
                action={
                  filtered ? (
                    <Button onClick={clearFilters}>Clear filters</Button>
                  ) : (
                    <CollectJobsAction onComplete={() => void load(0, false)} />
                  )
                }
              />
            </div>
          ) : (
            <>
              <p className="tabular mt-2 text-xs text-slate-500">
                Showing {jobs.length} of {total} · {pageSize} per page
              </p>
              <ul className="mt-1 divide-y divide-slate-100">
                {jobs.map((job) => {
                  const salary = salaryLabel(job);
                  return (
                    <li
                      key={job.job_id}
                      className={`py-3 transition-colors duration-150 hover:bg-slate-50/80 ${
                        job.saved ? "bg-emerald-50/30" : ""
                      } ${flashId === job.job_id ? "animate-row-flash" : ""}`}
                    >
                      <div className="flex flex-wrap items-start gap-3">
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
                            {salary ? <span className="tabular">{salary}</span> : null}
                            <ScoreMeter value={job.fit_score} />
                            <RiskBadge
                              riskClass={job.hidden_by_policy ? null : job.risk_class}
                              confidence={job.risk_confidence}
                              label={job.risk_label}
                              compact
                            />
                            {job.hidden_by_policy ? (
                              <span className="rounded-full bg-slate-200 px-2 py-0.5 text-slate-700">
                                Hidden by policy
                              </span>
                            ) : null}
                          </div>
                        </div>
                        <div className="flex w-full shrink-0 flex-wrap items-center gap-2 pl-[3.25rem] sm:w-auto sm:pl-0">
                          <Button
                            variant={job.saved ? "secondary" : "primary"}
                            disabled={savingId === job.job_id}
                            onClick={() => void toggleSaved(job)}
                          >
                            {job.saved ? "Saved ✓" : "Save"}
                          </Button>
                          <Disclosure summary="Why this rank">
                            <dl className="grid grid-cols-2 gap-2 text-xs text-slate-600">
                              <div>
                                <dt className="text-slate-500">Rank</dt>
                                <dd className="tabular">
                                  {typeof job.rank_score === "number"
                                    ? job.rank_score.toFixed(2)
                                    : "not reported"}
                                </dd>
                              </div>
                              <div>
                                <dt className="text-slate-500">Credibility</dt>
                                <dd className="tabular">
                                  {typeof job.credibility === "number"
                                    ? job.credibility.toFixed(2)
                                    : "not reported"}
                                </dd>
                              </div>
                            </dl>
                            <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-slate-600">
                              {(job.rank_reasons ?? []).length === 0 ? (
                                <li>No ranking evidence recorded yet.</li>
                              ) : (
                                (job.rank_reasons ?? []).map((reason, index) => (
                                  <li key={index}>{reason}</li>
                                ))
                              )}
                            </ul>
                          </Disclosure>
                        </div>
                      </div>
                    </li>
                  );
                })}
              </ul>
              <Pagination
                shown={jobs.length}
                total={total}
                loading={busy}
                noun="matches"
                pageSize={pageSize}
                onLoadMore={() => void load(jobs.length, true)}
              />
            </>
          )}
        </section>
      )}

      {!workflow.loading && !workflow.error && !focusOpen ? (
        <WorkflowNextStepBar state={workflow} />
      ) : null}
      <p className="text-xs text-slate-400">
        Nothing here submits an application.{" "}
        <Link to="/saved" className="underline">
          Your shortlist
        </Link>{" "}
        is where you apply.
      </p>
    </div>
  );
}
