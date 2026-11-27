import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Tabs } from "../../components/Tabs";
import { InfoTip } from "../../components/InfoTip";
import { PageHeader } from "../../components/PageHeader";
import { Copy } from "../../copy/copy";
import { announceCountsChanged, useSetupStatus } from "../../hooks/useSetupStatus";
import { pickResumeFile } from "../../services/filePicker";
import { workerClient, WorkerError } from "../../services/workerClient";
import { useLiveRefresh } from "../../hooks/useLiveRefresh";
import type {
  ResumeImportResponse,
  ResumeSetSkillStatusRequest,
} from "../../types/generated";
import { VersionPreview } from "./VersionPreview";
import {
  isActiveVersion,
  orderedVersions,
  previousVersion,
  toVersionRow,
  versionLabel,
  type ResumeVersionRow,
} from "./resumeVersions";

function formatImportedAt(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleDateString();
}

const DIMENSION_LABELS: Record<string, string> = {
  skills_resume: "Skills",
  experience_seniority: "Experience",
  responsibilities: "Responsibilities",
  location_workplace: "Location",
  compensation: "Compensation",
  freshness: "Freshness",
};

const DEFAULT_WEIGHTS: Record<string, number> = {
  skills_resume: 0.35,
  experience_seniority: 0.2,
  responsibilities: 0.2,
  location_workplace: 0.1,
  compensation: 0.1,
  freshness: 0.05,
};

type Dimension = {
  dimension: string;
  score: number | null;
  available: boolean;
  weight: number;
  details?: { summary?: string; reasons?: string[] };
};

type Explanation = { reason_code: string; message: string };

type MatchResult = {
  result_id: string;
  job_id: string;
  job_title?: string | null;
  company_name?: string | null;
  //: Missing values stay null so the UI can say "not assessed", never 0.00.
  fit_score: number | null;
  dimension_coverage: number | null;
  dimensions?: Dimension[];
  explanations?: Explanation[];
  location_text?: string;
  workplace_type?: string;
  required_skills?: string[];
  preferred_skills?: string[];
  missing_required_skills?: string[];
  required_skill_coverage?: number | null;
  saved?: boolean;
  outside_area?: boolean;
  hidden_for_missing_skills?: boolean;
};

type SkillRow = {
  keyword_id: string;
  evidence: string;
  confidence: number;
  status?: string | null;
};

type ExperienceRow = {
  employer: string;
  title: string;
  start: string;
  end: string;
};

type EducationRow = {
  institution: string;
  degree: string;
  field: string;
  start: string;
  end: string;
};

type MatchRunSummary = {
  available_jobs?: number;
  considered?: number;
  scored?: number;
  reused?: number;
  truncated?: boolean;
};

const MONTH_NAMES = [
  "jan",
  "feb",
  "mar",
  "apr",
  "may",
  "jun",
  "jul",
  "aug",
  "sep",
  "oct",
  "nov",
  "dec",
];

/** True for the ways a resume writes "still there" as an end date. */
function isPresent(value: string): boolean {
  const text = (value ?? "").trim().toLowerCase();
  return ["present", "current", "now", "today"].includes(text);
}

/** Read a stored date as an `<input type="month">` value (YYYY-MM). */
function monthValue(value: string): string {
  const text = (value ?? "").trim();
  if (!text || isPresent(text)) return "";
  let match = /^(\d{4})-(\d{2})$/.exec(text);
  if (match) return `${match[1]}-${match[2]}`;
  match = /^(\d{1,2})\/(\d{4})$/.exec(text);
  if (match) {
    const month = Number(match[1]);
    return month >= 1 && month <= 12
      ? `${match[2]}-${String(month).padStart(2, "0")}`
      : "";
  }
  match = /^([A-Za-z]+)\.?\s*,?\s*(\d{4})$/.exec(text);
  if (match) {
    const index = MONTH_NAMES.indexOf(match[1].slice(0, 3).toLowerCase());
    if (index >= 0) return `${match[2]}-${String(index + 1).padStart(2, "0")}`;
  }
  match = /^(\d{4})$/.exec(text);
  if (match) return `${match[1]}-01`;
  return "";
}

/** Keep an unreadable stored date as text instead of silently dropping it. */
function storedDate(value: string): string {
  const text = (value ?? "").trim();
  if (!text) return "";
  return monthValue(text) || text;
}

/** Total years across merged ranges, matching the backend's calculation. */
function yearsFromExperience(rows: ExperienceRow[]): number | null {
  const now = Date.now();
  const ranges: Array<[number, number]> = [];
  for (const row of rows) {
    const start = monthValue(row.start);
    if (!/^\d{4}-\d{2}$/.test(start)) continue;
    const startAt = Date.parse(`${start}-01T00:00:00Z`);
    let endAt: number;
    if (isPresent(row.end)) {
      endAt = now;
    } else {
      const end = monthValue(row.end);
      if (!/^\d{4}-\d{2}$/.test(end)) continue;
      endAt = Date.parse(`${end}-01T00:00:00Z`);
    }
    if (Number.isFinite(startAt) && Number.isFinite(endAt) && endAt >= startAt) {
      ranges.push([startAt, endAt]);
    }
  }
  if (ranges.length === 0) return null;
  ranges.sort((left, right) => left[0] - right[0]);
  let total = 0;
  let [currentStart, currentEnd] = ranges[0];
  for (const [start, end] of ranges.slice(1)) {
    if (start <= currentEnd) {
      currentEnd = Math.max(currentEnd, end);
      continue;
    }
    total += currentEnd - currentStart;
    currentStart = start;
    currentEnd = end;
  }
  total += currentEnd - currentStart;
  return Math.round((total / (365.25 * 86_400_000)) * 10) / 10;
}

type ItemRow = { name?: string | null; details?: string | null };

type ContactForm = {
  name: string;
  email: string;
  phone: string;
  linkedin: string;
  github: string;
  website: string;
};

type PreferenceForm = {
  homeLocation: string;
  authorizedCountries: string;
  locations: string;
  workplaceTypes: string;
  remoteOk: boolean;
  relocation: boolean;
  salaryMin: string;
  salaryTarget: string;
};

function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function stringRecord(value: unknown): Record<string, string> {
  if (!value || typeof value !== "object") return {};
  const out: Record<string, string> = {};
  for (const [key, entry] of Object.entries(value as Record<string, unknown>)) {
    if (typeof entry === "string") out[key] = entry;
  }
  return out;
}

function toExperienceRows(value: unknown): ExperienceRow[] {
  if (!Array.isArray(value)) return [];
  return value.map((raw) => {
    const row = (raw ?? {}) as Record<string, unknown>;
    const pick = (...keys: string[]): string => {
      for (const key of keys) {
        if (typeof row[key] === "string") return row[key] as string;
      }
      return "";
    };
    return {
      employer: pick("employer"),
      title: pick("title"),
      start: storedDate(pick("start", "start_date")),
      end: isPresent(pick("end", "end_date"))
        ? "present"
        : storedDate(pick("end", "end_date")),
    };
  });
}

/** Parser entries use start/end; stored rows use start_date/end_date. */
function toEducationRows(value: unknown): EducationRow[] {
  if (!Array.isArray(value)) return [];
  return value.map((raw) => {
    const row = (raw ?? {}) as Record<string, unknown>;
    const pick = (...keys: string[]): string => {
      for (const key of keys) {
        if (typeof row[key] === "string") return row[key] as string;
      }
      return "";
    };
    const end = pick("end", "end_date");
    return {
      institution: pick("institution"),
      degree: pick("degree"),
      field: pick("field"),
      start: storedDate(pick("start", "start_date")),
      end: isPresent(end) ? "present" : storedDate(end),
    };
  });
}

function MonthInput({
  value,
  onChange,
  ariaLabel,
}: {
  value: string;
  onChange: (value: string) => void;
  ariaLabel: string;
}) {
  const normalized = /^\d{4}-\d{2}$/.test(value) ? value : "";
  const unreadable = value.trim() !== "" && normalized === "";
  return (
    <input
      type={unreadable ? "text" : "month"}
      className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
      aria-label={ariaLabel}
      title={
        unreadable
          ? "This stored value is not a month; edit it or pick one from the calendar."
          : undefined
      }
      value={unreadable ? value : normalized}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

function parseMatchResults(rows: unknown[] | undefined): MatchResult[] {
  return (rows ?? []).map((raw) => {
    const row = (raw ?? {}) as Record<string, unknown>;
    return {
      result_id: String(row.result_id ?? ""),
      job_id: String(row.job_id ?? ""),
      job_title: typeof row.job_title === "string" ? row.job_title : null,
      company_name: typeof row.company_name === "string" ? row.company_name : null,
      fit_score: numberOrNull(row.fit_score),
      dimension_coverage: numberOrNull(row.dimension_coverage),
      location_text: typeof row.location_text === "string" ? row.location_text : "",
      workplace_type: typeof row.workplace_type === "string" ? row.workplace_type : "",
      required_skills: Array.isArray(row.required_skills)
        ? row.required_skills.map(String)
        : [],
      preferred_skills: Array.isArray(row.preferred_skills)
        ? row.preferred_skills.map(String)
        : [],
      missing_required_skills: Array.isArray(row.missing_required_skills)
        ? row.missing_required_skills.map(String)
        : [],
      required_skill_coverage: numberOrNull(row.required_skill_coverage),
      saved: Boolean(row.saved),
      outside_area: Boolean(row.outside_area),
      hidden_for_missing_skills: Boolean(row.hidden_for_missing_skills),
      dimensions: Array.isArray(row.dimensions)
        ? (row.dimensions as Dimension[]).map((dimension) => ({
            ...dimension,
            score: numberOrNull(dimension.score),
          }))
        : [],
      explanations: Array.isArray(row.explanations)
        ? (row.explanations as Explanation[])
        : [],
    };
  });
}

export function ResumeRoute() {
  const setup = useSetupStatus();
  const [tab, setTab] = useState("resume");
  const [filePath, setFilePath] = useState("");
  const [label, setLabel] = useState("Imported");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState(false);
  const [importResult, setImportResult] = useState<ResumeImportResponse | null>(null);
  const [versions, setVersions] = useState<ResumeVersionRow[]>([]);
  const [openVersionId, setOpenVersionId] = useState<string | null>(null);
  const [matchResults, setMatchResults] = useState<MatchResult[]>([]);
  const [matchSummary, setMatchSummary] = useState<MatchRunSummary | null>(null);
  const [savedMatches, setSavedMatches] = useState<Set<string>>(new Set());
  const [savingMatchId, setSavingMatchId] = useState<string | null>(null);
  const [includeOutOfArea, setIncludeOutOfArea] = useState(false);
  const [includeMissingSkills, setIncludeMissingSkills] = useState(false);
  const [hiddenOutOfArea, setHiddenOutOfArea] = useState(0);
  const [hiddenMissingSkills, setHiddenMissingSkills] = useState(0);
  const [weights, setWeights] = useState<Record<string, number>>(DEFAULT_WEIGHTS);
  const [skills, setSkills] = useState<SkillRow[]>([]);
  const [skillBusy, setSkillBusy] = useState<string | null>(null);
  const [newSkill, setNewSkill] = useState("");
  const [profileOverrides, setProfileOverrides] = useState<Record<string, unknown>>({});
  const [contact, setContact] = useState<ContactForm>({
    name: "",
    email: "",
    phone: "",
    linkedin: "",
    github: "",
    website: "",
  });
  const [experience, setExperience] = useState<ExperienceRow[]>([]);
  const [education, setEducation] = useState<EducationRow[]>([]);
  const [certifications, setCertifications] = useState<ItemRow[]>([]);
  const [projects, setProjects] = useState<ItemRow[]>([]);
  const [preferences, setPreferences] = useState<PreferenceForm>({
    homeLocation: "",
    authorizedCountries: "",
    locations: "",
    workplaceTypes: "",
    remoteOk: false,
    relocation: false,
    salaryMin: "",
    salaryTarget: "",
  });

  const loadVersions = useCallback(async () => {
    try {
      const r = await workerClient.listResumeVersions({ profile_id: "default" });
      setVersions(
        (r.versions ?? [])
          .map(toVersionRow)
          .filter((row): row is ResumeVersionRow => row !== null),
      );
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    }
  }, []);

  const loadMatching = useCallback(async () => {
    try {
      await workerClient.runMatching({ profile_id: "default" });
      const [results, profile, saved] = await Promise.all([
        workerClient.getMatchingResults({
          profile_id: "default",
          limit: 100,
          include_out_of_area: includeOutOfArea,
          include_missing_skills: includeMissingSkills,
        }),
        workerClient.getResumeProfile({ profile_id: "default" }),
        workerClient.listSavedJobs({ view: "all", limit: 200 }),
      ]);
      setMatchResults(parseMatchResults(results.results));
      setHiddenOutOfArea(results.hidden_out_of_area ?? 0);
      setHiddenMissingSkills(results.hidden_missing_skills ?? 0);
      setSavedMatches(new Set((saved.jobs ?? []).map((row) => row.job_id)));
      setWeights({ ...DEFAULT_WEIGHTS, ...(results.weights ?? {}) });
      const stored = profile.profile as {
        skills?: SkillRow[];
        overrides_json?: string;
        contact?: Record<string, unknown>;
        contact_json?: string;
        experience?: unknown;
        education?: unknown;
        certifications?: ItemRow[];
        projects?: ItemRow[];
      } | null | undefined;
      setSkills(stored?.skills ?? []);
      let legacyContact: unknown;
      try {
        legacyContact = stored?.contact_json ? JSON.parse(stored.contact_json) : undefined;
      } catch {
        legacyContact = undefined;
      }
      const storedContact = stringRecord(stored?.contact ?? legacyContact);
      setContact({
        name: storedContact.name ?? "",
        email: storedContact.email ?? "",
        phone: storedContact.phone ?? "",
        linkedin: storedContact.linkedin ?? "",
        github: storedContact.github ?? "",
        website: storedContact.website ?? "",
      });
      setExperience(toExperienceRows(stored?.experience));
      setEducation(toEducationRows(stored?.education));
      setCertifications(Array.isArray(stored?.certifications) ? stored.certifications : []);
      setProjects(Array.isArray(stored?.projects) ? stored.projects : []);
      let overrides: Record<string, unknown> = {};
      if (typeof stored?.overrides_json === "string") {
        try {
          const parsed = JSON.parse(stored.overrides_json) as Record<string, unknown>;
          if (parsed && typeof parsed === "object") overrides = parsed;
        } catch {
          overrides = {};
        }
      }
      setProfileOverrides(overrides);
      const prefs = (overrides.preferences ?? overrides) as Record<string, unknown>;
      setPreferences({
        homeLocation: typeof prefs.home_location === "string" ? prefs.home_location : "",
        authorizedCountries: Array.isArray(prefs.authorized_countries)
          ? prefs.authorized_countries.join(", ")
          : "",
        locations: Array.isArray(prefs.locations) ? prefs.locations.join(", ") : "",
        workplaceTypes: Array.isArray(prefs.workplace_types)
          ? prefs.workplace_types.join(", ")
          : "",
        remoteOk: Boolean(prefs.remote_ok),
        relocation: Boolean(prefs.relocation),
        salaryMin: prefs.desired_salary_min != null ? String(prefs.desired_salary_min) : "",
        salaryTarget: prefs.desired_salary_target != null ? String(prefs.desired_salary_target) : "",
      });
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    }
  }, [includeOutOfArea, includeMissingSkills]);

  useEffect(() => {
    void loadVersions();
    void loadMatching();
  }, [loadVersions, loadMatching]);
  useLiveRefresh(() => void loadMatching());

  const chooseFile = async () => {
    setPicking(true);
    try {
      const picked = await pickResumeFile();
      if (picked) {
        setFilePath(picked);
        setError(null);
      }
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setPicking(false);
    }
  };

  const doImport = async () => {
    if (!filePath.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const r = await workerClient.importResume({ file_path: filePath.trim(), label });
      setImportResult(r);
      setFilePath("");
      announceCountsChanged();
      await loadVersions();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const makeActive = async (versionId: string) => {
    setBusy(true);
    setError(null);
    try {
      await workerClient.setResumeActive({ profile_id: "default", version_id: versionId });
      await loadVersions();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const runMatching = async () => {
    setBusy(true);
    setError(null);
    try {
      const run = await workerClient.runMatching({ profile_id: "default" });
      setMatchSummary(run);
      await loadMatching();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const saveWeights = async () => {
    setBusy(true);
    setError(null);
    try {
      const response = await workerClient.updateMatchingWeights({
        profile_id: "default",
        weights,
      });
      setWeights({ ...DEFAULT_WEIGHTS, ...(response.weights ?? {}) });
      await workerClient.runMatching({ profile_id: "default" });
      await loadMatching();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const changeSkillStatus = async (keywordId: string, status: string) => {
    setSkillBusy(keywordId);
    setError(null);
    try {
      await workerClient.setResumeSkillStatus({
        profile_id: "default",
        keyword_id: keywordId,
        status: status as ResumeSetSkillStatusRequest["status"],
      });
      setSkills((current) =>
        current.map((skill) =>
          skill.keyword_id === keywordId ? { ...skill, status } : skill,
        ),
      );
      announceCountsChanged();
      await workerClient.runMatching({ profile_id: "default" });
      await loadMatching();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setSkillBusy(null);
    }
  };

  const addSkill = async () => {
    const value = newSkill.trim();
    if (!value) return;
    setBusy(true);
    setError(null);
    try {
      const added = Array.isArray(profileOverrides.skills_added)
        ? (profileOverrides.skills_added as unknown[]).map(String)
        : [];
      const overrides = { ...profileOverrides, skills_added: [...added, value] };
      await workerClient.updateResumeOverrides({ profile_id: "default", overrides });
      setProfileOverrides(overrides);
      setNewSkill("");
      announceCountsChanged();
      await workerClient.runMatching({ profile_id: "default" });
      await loadMatching();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const toggleMatchSaved = async (result: MatchResult) => {
    const next = !savedMatches.has(result.job_id);
    setSavingMatchId(result.job_id);
    setSavedMatches((current) => {
      const copy = new Set(current);
      if (next) copy.add(result.job_id);
      else copy.delete(result.job_id);
      return copy;
    });
    try {
      await workerClient.setJobUserState({
        job_id: result.job_id,
        state: next ? "saved" : "new",
      });
      announceCountsChanged();
    } catch (exc) {
      setSavedMatches((current) => {
        const copy = new Set(current);
        if (next) copy.delete(result.job_id);
        else copy.add(result.job_id);
        return copy;
      });
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setSavingMatchId(null);
    }
  };

  const savePreferences = async () => {
    setBusy(true);
    setError(null);
    try {
      const split = (value: string) =>
        value.split(",").map((item) => item.trim()).filter(Boolean);
      await workerClient.updateResumeOverrides({
        profile_id: "default",
        overrides: {
          ...profileOverrides,
          preferences: {
            home_location: preferences.homeLocation.trim(),
            authorized_countries: split(preferences.authorizedCountries),
            locations: split(preferences.locations),
            workplace_types: split(preferences.workplaceTypes),
            remote_ok: preferences.remoteOk,
            relocation: preferences.relocation,
            desired_salary_min: preferences.salaryMin ? Number(preferences.salaryMin) : null,
            desired_salary_target: preferences.salaryTarget
              ? Number(preferences.salaryTarget)
              : null,
          },
        },
      });
      announceCountsChanged();
      await workerClient.runMatching({ profile_id: "default" });
      await loadMatching();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const saveProfileDetails = async () => {
    setBusy(true);
    setError(null);
    try {
      const rows = experience
        .filter((row) => row.employer.trim() || row.title.trim())
        .map((row) => ({
          employer: row.employer.trim(),
          title: row.title.trim(),
          start: storedDate(row.start),
          end: isPresent(row.end) ? "present" : storedDate(row.end),
        }));
      const educationRows = education
        .filter(
          (row) => row.institution.trim() || row.degree.trim() || row.field.trim(),
        )
        .map((row) => ({
          institution: row.institution.trim(),
          degree: row.degree.trim(),
          field: row.field.trim(),
          start: storedDate(row.start),
          end: isPresent(row.end) ? "present" : storedDate(row.end),
        }));
      const years = yearsFromExperience(experience);
      const overrides = {
        ...profileOverrides,
        contact: Object.fromEntries(
          Object.entries(contact).filter(([, value]) => value.trim() !== ""),
        ),
        experience: rows,
        titles: rows.map((row) => row.title).filter(Boolean),
        education: educationRows,
        ...(years !== null ? { years_experience_total: years } : {}),
      };
      await workerClient.updateResumeOverrides({ profile_id: "default", overrides });
      setProfileOverrides(overrides);
      announceCountsChanged();
      await workerClient.runMatching({ profile_id: "default" });
      await loadMatching();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
    }
  };

  const updateExperience = (index: number, patch: Partial<ExperienceRow>) => {
    setExperience((current) =>
      current.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row)),
    );
  };

  const updateEducation = (index: number, patch: Partial<EducationRow>) => {
    setEducation((current) =>
      current.map((row, rowIndex) => (rowIndex === index ? { ...row, ...patch } : row)),
    );
  };

  const totalWeight = Object.values(weights).reduce((sum, value) => sum + value, 0) || 1;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Profile"
        description="Your resume and preferences power fit scores. Everything stays on this computer."
      />

      {setup.checked && setup.missing.length > 0 ? (
        <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          Setup still needed: {setup.missing.join(", ")}. Matching needs a resume,
          and discovery needs at least one job source.
        </p>
      ) : null}

      <Tabs
        label="Profile sections"
        active={tab}
        onChange={setTab}
        items={[
          { id: "resume", label: "Resume" },
          { id: "profile", label: "Profile details" },
          { id: "preferences", label: "Preferences" },
          { id: "matches", label: "Matches" },
        ]}
      />

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-800">{error}</div>
      ) : null}

      <div className={tab === "resume" ? "space-y-6" : "hidden"}>
      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900" aria-label="Import resume">
          Import resume
          <InfoTip label="Import resume">{Copy.tooltips.importResume}</InfoTip>
        </h2>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void chooseFile()}
            disabled={picking || busy}
            className="rounded border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50 disabled:opacity-50"
          >
            {picking ? "Choosing…" : "Choose file…"}
          </button>
          <input
            className="min-w-0 flex-1 rounded border border-slate-300 px-3 py-2 text-sm"
            placeholder="No file selected (or paste a full path)"
            aria-label="Resume file path"
            value={filePath}
            onChange={(e) => setFilePath(e.target.value)}
          />
          <input
            className="w-32 rounded border border-slate-300 px-3 py-2 text-sm"
            placeholder="Version label"
            aria-label="Version label"
            title={Copy.tooltips.versionLabel}
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
          <button
            type="button"
            onClick={() => void doImport()}
            disabled={busy}
            className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {busy ? "Importing…" : "Import resume"}
          </button>
        </div>
        {importResult ? (
          <div className="mt-3 rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">
            Imported as version {importResult.version_id}. Skills found:{" "}
            {(importResult.skills ?? []).join(", ") || "none"}. Sections found:{" "}
            {(importResult.sections ?? []).join(", ") || "none"}.
            {typeof importResult.years_experience === "number"
              ? ` Estimated experience: ~${importResult.years_experience} years (inferred).`
              : ""}
            {importResult.contact?.email ? ` Contact: ${importResult.contact.email}.` : ""}
          </div>
        ) : null}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2
          className="text-lg font-semibold text-slate-900"
          aria-label={`Versions (${versions.length})`}
        >
          Versions ({versions.length})
          <InfoTip label="Versions">{Copy.tooltips.activeVersion}</InfoTip>
        </h2>
        <ul className="mt-3 divide-y divide-slate-100">
          {orderedVersions(versions).map((v) => {
            const active = isActiveVersion(v);
            const open = openVersionId === v.id;
            return (
              <li key={v.id} className="py-2 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="min-w-0">
                    <span className="font-medium text-slate-900">{versionLabel(v)}</span>
                    <span className="ml-2 text-xs text-slate-500">
                      {v.parser_id ?? "parser not reported"}
                      {v.created_at ? ` · ${formatImportedAt(v.created_at)}` : ""}
                      {v.original_name ? ` · ${v.original_name}` : ""}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    {active ? (
                      <span className="rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-800">
                        Active
                      </span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => void makeActive(v.id)}
                        disabled={busy}
                        className="rounded border border-slate-300 bg-white px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      >
                        Use for matching
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => setOpenVersionId(open ? null : v.id)}
                      aria-expanded={open}
                      className="rounded px-2 py-1 text-xs font-medium text-slate-600 underline hover:text-slate-900"
                    >
                      {open ? "Hide preview" : "Preview text"}
                    </button>
                  </div>
                </div>
                {open ? (
                  <VersionPreview version={v} previous={previousVersion(versions, v.id)} />
                ) : null}
              </li>
            );
          })}
          {versions.length === 0 ? (
            <li className="text-sm text-slate-500">
              No resume versions yet. Import one above to start matching.
            </li>
          ) : null}
        </ul>
      </section>
      </div>

      <div className={tab === "profile" ? "space-y-6" : "hidden"}>
      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900" aria-label="Profile details">
          Profile details
          <InfoTip label="Profile details">{Copy.tooltips.overrides}</InfoTip>
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          These edits override the parsed resume for matching. The imported file is never
          modified.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {(
            [
              ["name", "Name"],
              ["email", "Email"],
              ["phone", "Phone"],
              ["linkedin", "LinkedIn"],
              ["github", "GitHub"],
              ["website", "Website"],
            ] as const
          ).map(([key, label]) => (
            <label key={key} className="text-sm text-slate-700">
              {label}
              <input
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
                value={contact[key]}
                onChange={(event) =>
                  setContact((current) => ({ ...current, [key]: event.target.value }))
                }
              />
            </label>
          ))}
        </div>

        <h3 className="mt-4 text-sm font-semibold text-slate-900">Experience</h3>
        <div className="mt-2 space-y-2">
          {experience.map((row, index) => (
            <div key={index} className="grid gap-2 sm:grid-cols-4">
              <input
                className="rounded border border-slate-300 px-2 py-1 text-sm"
                placeholder="Employer"
                value={row.employer}
                onChange={(event) => updateExperience(index, { employer: event.target.value })}
              />
              <input
                className="rounded border border-slate-300 px-2 py-1 text-sm"
                placeholder="Title"
                value={row.title}
                onChange={(event) => updateExperience(index, { title: event.target.value })}
              />
              <MonthInput
                ariaLabel={`Experience start ${index + 1}`}
                value={row.start}
                onChange={(value) => updateExperience(index, { start: value })}
              />
              <div className="flex flex-wrap items-center gap-2">
                <MonthInput
                  ariaLabel={`Experience end ${index + 1}`}
                  value={isPresent(row.end) ? "" : row.end}
                  onChange={(value) => updateExperience(index, { end: value })}
                />
                <label className="flex items-center gap-1 text-xs text-slate-600">
                  <input
                    type="checkbox"
                    checked={isPresent(row.end)}
                    onChange={(event) =>
                      updateExperience(index, {
                        end: event.target.checked ? "present" : "",
                      })
                    }
                  />
                  Present
                </label>
                <button
                  type="button"
                  className="rounded border border-slate-300 px-2 text-xs text-slate-700 hover:bg-slate-50"
                  onClick={() =>
                    setExperience((current) => current.filter((_, i) => i !== index))
                  }
                >
                  Remove
                </button>
              </div>
            </div>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            className="rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
            onClick={() =>
              setExperience((current) => [
                ...current,
                { employer: "", title: "", start: "", end: "" },
              ])
            }
          >
            Add experience
          </button>
          <button
            type="button"
            onClick={() => void saveProfileDetails()}
            disabled={busy}
            className="rounded bg-slate-900 px-3 py-1 text-xs font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            Save profile details
          </button>
        </div>

        <div className="mt-4">
          <h3 className="text-sm font-semibold text-slate-900">Education</h3>
          <p className="mt-1 text-xs text-slate-500">
            Parsed from your resume and editable here. A saved row overrides the parse for
            matching; the file itself is never changed.
          </p>
          <div className="mt-2 space-y-2">
            {education.map((row, index) => (
              <div key={index} className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                <input
                  className="rounded border border-slate-300 px-2 py-1 text-sm"
                  aria-label={`Education institution ${index + 1}`}
                  placeholder="Institution"
                  value={row.institution}
                  onChange={(event) =>
                    updateEducation(index, { institution: event.target.value })
                  }
                />
                <input
                  className="rounded border border-slate-300 px-2 py-1 text-sm"
                  aria-label={`Education degree ${index + 1}`}
                  placeholder="Degree (B.S.)"
                  value={row.degree}
                  onChange={(event) => updateEducation(index, { degree: event.target.value })}
                />
                <input
                  className="rounded border border-slate-300 px-2 py-1 text-sm"
                  aria-label={`Education field ${index + 1}`}
                  placeholder="Field (Computer Science)"
                  value={row.field}
                  onChange={(event) => updateEducation(index, { field: event.target.value })}
                />
                <div className="flex flex-wrap items-center gap-2 sm:col-span-2 lg:col-span-3">
                  <MonthInput
                    ariaLabel={`Education start ${index + 1}`}
                    value={row.start}
                    onChange={(value) => updateEducation(index, { start: value })}
                  />
                  <MonthInput
                    ariaLabel={`Education end ${index + 1}`}
                    value={isPresent(row.end) ? "" : row.end}
                    onChange={(value) => updateEducation(index, { end: value })}
                  />
                  <label className="flex items-center gap-1 text-xs text-slate-600">
                    <input
                      type="checkbox"
                      checked={isPresent(row.end)}
                      onChange={(event) =>
                        updateEducation(index, {
                          end: event.target.checked ? "present" : "",
                        })
                      }
                    />
                    In progress
                  </label>
                  <button
                    type="button"
                    className="rounded border border-slate-300 px-2 text-xs text-slate-700 hover:bg-slate-50"
                    onClick={() =>
                      setEducation((current) => current.filter((_, i) => i !== index))
                    }
                  >
                    Remove
                  </button>
                </div>
              </div>
            ))}
          </div>
          <button
            type="button"
            className="mt-2 rounded border border-slate-300 px-2 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50"
            onClick={() =>
              setEducation((current) => [
                ...current,
                { institution: "", degree: "", field: "", start: "", end: "" },
              ])
            }
          >
            Add education
          </button>
        </div>
        {certifications.length > 0 || projects.length > 0 ? (
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            {certifications.length > 0 ? (
              <div>
                <h3 className="text-sm font-semibold text-slate-900">Certifications</h3>
                <ul className="mt-1 list-disc pl-5 text-xs text-slate-600">
                  {certifications.map((row, index) => (
                    <li key={index}>{row.name}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            {projects.length > 0 ? (
              <div>
                <h3 className="text-sm font-semibold text-slate-900">Projects</h3>
                <ul className="mt-1 list-disc pl-5 text-xs text-slate-600">
                  {projects.map((row, index) => (
                    <li key={index}>{row.name}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900" aria-label="Skill status">
          Skill status
          <InfoTip label="Skill status">{Copy.tooltips.skillStatus}</InfoTip>
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          Set what you actually know for each skill. Your answer overrides what the parser
          found; “Not relevant” removes the skill from matching.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <input
            className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
            placeholder="Add a skill you have (e.g. Kubernetes)"
            value={newSkill}
            onChange={(event) => setNewSkill(event.target.value)}
          />
          <button
            type="button"
            onClick={() => void addSkill()}
            disabled={busy || !newSkill.trim()}
            className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            Add skill
          </button>
        </div>
        {skills.length > 0 ? (
          <ul className="mt-3 divide-y divide-slate-100">
            {skills.map((skill) => (
              <li key={skill.keyword_id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                <span className="font-medium text-slate-900">{skill.keyword_id}</span>
                <span className="text-xs text-slate-500">{skill.evidence}</span>
                <select
                  className="rounded border border-slate-300 px-2 py-1 text-xs"
                  value={skill.status ?? ""}
                  disabled={skillBusy === skill.keyword_id}
                  onChange={(event) => void changeSkillStatus(skill.keyword_id, event.target.value)}
                >
                  <option value="" disabled>Not set</option>
                  <option value="extensive_experience">Extensive experience (expert)</option>
                  <option value="have_experience">Have experience</option>
                  <option value="learning">Learning</option>
                  <option value="no_experience">No experience</option>
                  <option value="not_relevant">Not relevant</option>
                </select>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-slate-500">Import a resume to see its skills.</p>
        )}
      </section>

      </div>

      <div className={tab === "preferences" ? "space-y-6" : "hidden"}>
      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900" aria-label="Job preferences">
          Job preferences
          <InfoTip label="Job preferences">{Copy.tooltips.preferences}</InfoTip>
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          Used by the location and compensation parts of the fit score. Separate multiple
          values with commas.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <label className="text-sm text-slate-700">
            Home location
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              placeholder="Chicago, IL"
              value={preferences.homeLocation}
              onChange={(event) =>
                setPreferences((current) => ({
                  ...current,
                  homeLocation: event.target.value,
                }))
              }
            />
            <span className="mt-1 block text-xs text-slate-500">
              Used to check remote roles that are limited to a country.
            </span>
          </label>
          <label className="text-sm text-slate-700">
            Countries you can work in
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              placeholder="United States"
              value={preferences.authorizedCountries}
              onChange={(event) =>
                setPreferences((current) => ({
                  ...current,
                  authorizedCountries: event.target.value,
                }))
              }
            />
            <span className="mt-1 block text-xs text-slate-500">
              Comma-separated. Defaults to the country of your home location.
            </span>
          </label>
          <label className="text-sm text-slate-700">
            Preferred locations
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              placeholder="Berlin, London"
              value={preferences.locations}
              onChange={(event) =>
                setPreferences((current) => ({ ...current, locations: event.target.value }))
              }
            />
          </label>
          <label className="text-sm text-slate-700">
            Workplace types
            <input
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              placeholder="remote, hybrid, onsite"
              value={preferences.workplaceTypes}
              onChange={(event) =>
                setPreferences((current) => ({
                  ...current,
                  workplaceTypes: event.target.value,
                }))
              }
            />
          </label>
          <label className="text-sm text-slate-700">
            Minimum salary
            <input
              type="number"
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              value={preferences.salaryMin}
              onChange={(event) =>
                setPreferences((current) => ({ ...current, salaryMin: event.target.value }))
              }
            />
          </label>
          <label className="text-sm text-slate-700">
            Target salary
            <input
              type="number"
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              value={preferences.salaryTarget}
              onChange={(event) =>
                setPreferences((current) => ({ ...current, salaryTarget: event.target.value }))
              }
            />
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={preferences.remoteOk}
              onChange={(event) =>
                setPreferences((current) => ({ ...current, remoteOk: event.target.checked }))
              }
            />
            Open to remote
          </label>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input
              type="checkbox"
              checked={preferences.relocation}
              onChange={(event) =>
                setPreferences((current) => ({ ...current, relocation: event.target.checked }))
              }
            />
            Open to relocation
          </label>
        </div>
        <button
          type="button"
          onClick={() => void savePreferences()}
          disabled={busy}
          className="mt-3 rounded border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50 disabled:opacity-50"
        >
          Save preferences
        </button>
      </section>

      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900" aria-label="Scoring weights">
          Scoring weights
          <InfoTip label="Scoring weights">{Copy.tooltips.weights}</InfoTip>
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          Each dimension's share of the fit score. Saving normalizes the weights to 100%
          and recomputes matches.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {Object.entries(weights).map(([dimension, value]) => (
            <label key={dimension} className="text-sm text-slate-700">
              <span className="flex items-center justify-between">
                <span>{DIMENSION_LABELS[dimension] ?? dimension}</span>
                <span className="text-xs text-slate-500">{Math.round((value / totalWeight) * 100)}%</span>
              </span>
              <input
                type="number"
                min={0}
                max={1}
                step={0.05}
                value={value}
                onChange={(event) =>
                  setWeights((current) => ({
                    ...current,
                    [dimension]: Number(event.target.value),
                  }))
                }
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              />
            </label>
          ))}
        </div>
        <button
          type="button"
          onClick={() => void saveWeights()}
          disabled={busy}
          className="mt-3 rounded border border-slate-300 bg-white px-3 py-2 text-sm font-medium text-slate-800 hover:bg-slate-50 disabled:opacity-50"
        >
          Save weights
        </button>
      </section>

      </div>

      <div className={tab === "matches" ? "space-y-6" : "hidden"}>
      <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900" aria-label="Matching">
          Matching
          <InfoTip label="Matching">{Copy.tooltips.matchResults}</InfoTip>
        </h2>
        <div className="mt-3 flex flex-wrap items-center gap-4">
          <button
            type="button"
            onClick={() => void runMatching()}
            disabled={busy}
            className="rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {busy ? "Matching…" : "Recompute matches"}
          </button>
          <label className="flex items-center gap-2 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={!includeOutOfArea}
              onChange={(event) => setIncludeOutOfArea(!event.target.checked)}
            />
            Only roles in my area
          </label>
          <label className="flex items-center gap-2 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={!includeMissingSkills}
              onChange={(event) => setIncludeMissingSkills(!event.target.checked)}
            />
            Only roles matching required skills
          </label>
        </div>
        {(!includeOutOfArea && hiddenOutOfArea > 0) ||
        (!includeMissingSkills && hiddenMissingSkills > 0) ? (
          <p className="mt-2 text-xs text-slate-500">
            {!includeOutOfArea && hiddenOutOfArea > 0
              ? `${hiddenOutOfArea} outside your area hidden. `
              : ""}
            {!includeMissingSkills && hiddenMissingSkills > 0
              ? `${hiddenMissingSkills} hidden for missing required skills. `
              : ""}
            {!includeOutOfArea && hiddenOutOfArea > 0 ? (
              <button
                type="button"
                className="font-medium text-brand-700 underline"
                onClick={() => setIncludeOutOfArea(true)}
              >
                Show outside area.
              </button>
            ) : null}{" "}
            {!includeMissingSkills && hiddenMissingSkills > 0 ? (
              <button
                type="button"
                className="font-medium text-brand-700 underline"
                onClick={() => setIncludeMissingSkills(true)}
              >
                Show missing-skill roles.
              </button>
            ) : null}
          </p>
        ) : null}
        {matchSummary ? (
          matchSummary.available_jobs === 0 ? (
            <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">
              Nothing has been collected yet, so there is nothing to score. On Find
              jobs, use “Find new jobs everywhere”, then recompute.
            </p>
          ) : (
            <p className="mt-3 text-xs text-slate-600">
              Scored {matchSummary.scored ?? 0} of {matchSummary.considered ?? 0} jobs
              {matchSummary.reused
                ? ` (${matchSummary.reused} unchanged and reused)`
                : ""}
              .{" "}
              {matchSummary.truncated
                ? `The run covers the ${matchSummary.considered ?? 0} most recent of ${matchSummary.available_jobs ?? 0} stored jobs.`
                : ""}
            </p>
          )
        ) : null}
        {matchResults.length > 0 ? (
          <ul className="mt-3 space-y-3">
            {matchResults.map((result) => (
              <li key={result.result_id} className="rounded-lg border border-slate-200 p-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <div className="font-medium text-slate-900">
                    <Link
                      to={`/jobs/${result.job_id}`}
                      className="hover:text-brand-700 hover:underline"
                    >
                      {result.job_title ?? `Job ${result.job_id}`}
                    </Link>
                    {result.company_name ? (
                      <Link
                        to={`/jobs/${result.job_id}`}
                        className="ml-2 text-xs font-normal text-slate-500 hover:text-brand-700 hover:underline"
                      >
                        {result.company_name}
                      </Link>
                    ) : null}
                  </div>
                  <div className="text-xs text-slate-600">
                    {result.fit_score === null ? (
                      <span
                        title={Copy.tooltips.fitScore}
                        className="rounded-full border border-slate-300 bg-slate-50 px-2 py-0.5 text-slate-600"
                      >
                        Not assessed yet
                      </span>
                    ) : (
                      <>
                        Fit score{" "}
                        <span title={Copy.tooltips.fitScore}>
                          {result.fit_score.toFixed(2)}
                        </span>
                      </>
                    )}{" "}
                    ·{" "}
                    {result.dimension_coverage === null ? (
                      <span title={Copy.tooltips.coverage}>data coverage not recorded</span>
                    ) : (
                      <>
                        data coverage{" "}
                        <span title={Copy.tooltips.coverage}>
                          {Math.round(result.dimension_coverage * 100)}%
                        </span>
                        {result.dimension_coverage < 0.6 ? (
                          <span
                            title={Copy.tooltips.limitedData}
                            className="ml-2 rounded-full border border-amber-200 bg-amber-50 px-2 py-0.5 text-amber-800"
                          >
                            limited data
                          </span>
                        ) : null}
                      </>
                    )}
                  </div>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
                  <span className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-slate-600">
                    {result.location_text || "Location not recorded"}
                  </span>
                  {result.required_skill_coverage !== null &&
                  result.required_skill_coverage !== undefined ? (
                    <span
                      className={
                        result.required_skill_coverage < 0.5
                          ? "rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-rose-800"
                          : "rounded-full border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-emerald-800"
                      }
                    >
                      Required skills {Math.round(result.required_skill_coverage * 100)}%
                    </span>
                  ) : null}
                  {(result.missing_required_skills ?? []).length > 0 ? (
                    <span className="rounded-full border border-rose-200 bg-rose-50 px-2 py-0.5 text-rose-800">
                      Missing: {(result.missing_required_skills ?? []).join(", ")}
                    </span>
                  ) : null}
                  <Link
                    to={`/jobs/${result.job_id}`}
                    className="rounded border border-slate-300 bg-white px-2 py-0.5 font-medium text-slate-700 hover:bg-slate-50"
                  >
                    View job
                  </Link>
                  <button
                    type="button"
                    disabled={savingMatchId === result.job_id}
                    onClick={() => void toggleMatchSaved(result)}
                    className="rounded border border-slate-300 bg-white px-2 py-0.5 font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                  >
                    {savedMatches.has(result.job_id) ? "Saved" : "Save for later"}
                  </button>
                </div>
                <details className="mt-2 text-xs text-slate-600">
                  <summary className="cursor-pointer font-medium text-slate-700">
                    How is this calculated?
                  </summary>
                  <p className="mt-1">
                    A weighted average of the dimensions that have data. Missing dimensions are
                    excluded, not scored zero. This is not a probability of an interview.
                  </p>
                  <ul className="mt-1 list-disc pl-5">
                    {Object.entries(weights).map(([dimension, value]) => (
                      <li key={dimension}>
                        {DIMENSION_LABELS[dimension] ?? dimension}:{" "}
                        {Math.round((value / totalWeight) * 100)}%
                      </li>
                    ))}
                  </ul>
                </details>
                <dl className="mt-3 space-y-1">
                  {(result.dimensions ?? []).map((dimension) => (
                    <div key={dimension.dimension} className="flex items-center gap-2 text-xs">
                      <dt className="w-28 shrink-0 text-slate-600">
                        {DIMENSION_LABELS[dimension.dimension] ?? dimension.dimension}
                      </dt>
                      <div className="h-1.5 flex-1 rounded bg-slate-100">
                        {dimension.available && typeof dimension.score === "number" ? (
                          <div
                            className="h-1.5 rounded bg-slate-700"
                            style={{ width: `${Math.round(dimension.score * 100)}%` }}
                          />
                        ) : null}
                      </div>
                      <dd className="w-24 text-right text-slate-500">
                        {dimension.available && dimension.score !== null
                          ? dimension.score.toFixed(2)
                          : "no data"}
                      </dd>
                    </div>
                  ))}
                </dl>
                {(result.explanations ?? []).length > 0 ? (
                  <ul className="mt-2 list-disc pl-5 text-xs text-slate-600">
                    {(result.explanations ?? []).map((explanation, index) => (
                      <li key={`${explanation.reason_code}-${index}`}>{explanation.message}</li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-3 text-sm text-slate-500">
            No matches yet. Use “Recompute matches” to score stored jobs.
          </p>
        )}
      </section>
      </div>
    </div>
  );
}
