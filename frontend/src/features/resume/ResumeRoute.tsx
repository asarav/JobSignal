import { useCallback, useEffect, useState } from "react";
import { Tabs } from "../../components/Tabs";
import { InfoTip } from "../../components/InfoTip";
import { PageHeader } from "../../components/PageHeader";
import { Copy } from "../../copy/copy";
import { pickResumeFile } from "../../services/filePicker";
import { workerClient, WorkerError } from "../../services/workerClient";
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
  institution?: string | null;
  degree?: string | null;
  field?: string | null;
  start_date?: string | null;
  end_date?: string | null;
};

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
      start: pick("start", "start_date"),
      end: pick("end", "end_date"),
    };
  });
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
      const [results, profile] = await Promise.all([
        workerClient.getMatchingResults({ profile_id: "default" }),
        workerClient.getResumeProfile({ profile_id: "default" }),
      ]);
      setMatchResults(parseMatchResults(results.results));
      setWeights({ ...DEFAULT_WEIGHTS, ...(results.weights ?? {}) });
      const stored = profile.profile as {
        skills?: SkillRow[];
        overrides_json?: string;
        contact?: Record<string, unknown>;
        contact_json?: string;
        experience?: unknown;
        education?: EducationRow[];
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
      setEducation(Array.isArray(stored?.education) ? stored.education : []);
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
  }, []);

  useEffect(() => {
    void loadVersions();
    void loadMatching();
  }, [loadVersions, loadMatching]);

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
      await workerClient.runMatching({ profile_id: "default", job_ids: [] });
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
      await workerClient.runMatching({ profile_id: "default", job_ids: [] });
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
      await workerClient.runMatching({ profile_id: "default", job_ids: [] });
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
      await workerClient.runMatching({ profile_id: "default", job_ids: [] });
      await loadMatching();
    } catch (exc) {
      setError(exc instanceof WorkerError ? `${exc.code}: ${exc.message}` : String(exc));
    } finally {
      setBusy(false);
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
      await workerClient.runMatching({ profile_id: "default", job_ids: [] });
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
          start: row.start.trim(),
          end: row.end.trim(),
        }));
      const overrides = {
        ...profileOverrides,
        contact: Object.fromEntries(
          Object.entries(contact).filter(([, value]) => value.trim() !== ""),
        ),
        experience: rows,
        titles: rows.map((row) => row.title).filter(Boolean),
      };
      await workerClient.updateResumeOverrides({ profile_id: "default", overrides });
      setProfileOverrides(overrides);
      await workerClient.runMatching({ profile_id: "default", job_ids: [] });
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

  const totalWeight = Object.values(weights).reduce((sum, value) => sum + value, 0) || 1;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Profile"
        description="Your resume and preferences power fit scores. Everything stays on this computer."
      />

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
              <input
                className="rounded border border-slate-300 px-2 py-1 text-sm"
                placeholder="Start (Jan 2020)"
                value={row.start}
                onChange={(event) => updateExperience(index, { start: event.target.value })}
              />
              <div className="flex gap-2">
                <input
                  className="min-w-0 flex-1 rounded border border-slate-300 px-2 py-1 text-sm"
                  placeholder="End (Present)"
                  value={row.end}
                  onChange={(event) => updateExperience(index, { end: event.target.value })}
                />
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

        {education.length > 0 ? (
          <div className="mt-4">
            <h3 className="text-sm font-semibold text-slate-900">Education</h3>
            <ul className="mt-1 list-disc pl-5 text-xs text-slate-600">
              {education.map((row, index) => (
                <li key={index}>
                  {[row.degree, row.field].filter(Boolean).join(", ")}
                  {row.institution ? ` — ${row.institution}` : ""}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
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
        <button
          type="button"
          onClick={() => void runMatching()}
          disabled={busy}
          className="mt-3 rounded bg-slate-900 px-3 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {busy ? "Matching…" : "Recompute matches"}
        </button>
        {matchResults.length > 0 ? (
          <ul className="mt-3 space-y-3">
            {matchResults.map((result) => (
              <li key={result.result_id} className="rounded-lg border border-slate-200 p-3">
                <div className="flex flex-wrap items-baseline justify-between gap-2">
                  <div className="font-medium text-slate-900">
                    {result.job_title ?? `Job ${result.job_id}`}
                    {result.company_name ? (
                      <span className="ml-2 text-xs font-normal text-slate-500">{result.company_name}</span>
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
