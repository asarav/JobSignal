import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { Envelope } from "../types/generated";

export interface WorkerBridge {
  /** True when the in-browser preview bridge is active instead of Tauri. */
  readonly isPreview: boolean;
  request(method: string, params: Record<string, unknown>): Promise<Envelope>;
  onEvent(handler: (event: Envelope) => void): Promise<UnlistenFn>;
}

function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

class TauriBridge implements WorkerBridge {
  readonly isPreview = false;

  async request(method: string, params: Record<string, unknown>): Promise<Envelope> {
    return invoke<Envelope>("worker_request", { method, params });
  }

  onEvent(handler: (event: Envelope) => void): Promise<UnlistenFn> {
    return listen<Envelope>("worker://event", (event) => handler(event.payload));
  }
}

/**
 * Browser-only development bridge.
 *
 * Lets `npm run dev` render the real screens without the Tauri shell. It is
 * never used inside the desktop app, and it is clearly labelled in the UI.
 */
class PreviewBridge implements WorkerBridge {
  readonly isPreview = true;
  private tasks: Array<Record<string, unknown>> = [];
  private listeners: Array<(event: Envelope) => void> = [];
  private versions: Array<Record<string, unknown>> = [
    previewVersion("preview-v1", "Sample resume (preview)", true, "sample"),
  ];
  private matchResults: Array<Record<string, unknown>> = [];
  private overrides: Record<string, unknown> = {};
  private weights: Record<string, number> = {
    skills_resume: 0.35,
    experience_seniority: 0.2,
    responsibilities: 0.2,
    location_workplace: 0.1,
    compensation: 0.1,
    freshness: 0.05,
  };

  async request(method: string, params: Record<string, unknown>): Promise<Envelope> {
    const response = (result: unknown): Envelope => ({
      v: 1,
      type: "response",
      id: "preview",
      params: {},
      ok: true,
      result: result as Record<string, unknown>,
    });

    switch (method) {
      case "system.hello":
        return response({
          protocol_version: 1,
          app_version: "0.0.0-preview",
          schema_version: 1,
          plugin_api_version: "1",
          capabilities: [
            "system.hello",
            "system.health",
            "system.shutdown",
            "tasks.list",
            "tasks.cancel",
            "settings.get",
            "settings.update",
            "plugins.list",
            "plugins.enable",
            "plugins.disable",
            "plugins.configure",
            "plugins.get_capabilities",
            "plugins.reset_circuit",
            "diagnostics.sleep",
            "diagnostics.fetch_url",
          ],
        });
      case "system.health":
        return response({
          status: "preview",
          app_version: "0.0.0-preview",
          schema_version: 1,
          protocol_version: 1,
          data_dir: "Preview mode (no Tauri bridge)",
          disk_usage_bytes: 0,
          plugin_health: previewPlugins(),
          queue_depth: this.tasks.filter((t) => t.status === "running").length,
        });
      case "settings.get":
      case "settings.update":
        return response({
          config: previewConfig(),
          overrides: {},
          schema_version: 1,
        });
      case "plugins.list":
        return response({ plugins: previewPlugins() });
      case "sources.list":
        return response({ sources: [] });
      case "jobs.list":
      case "jobs.search":
        return response({ jobs: previewJobs(), total: previewJobs().length });
      case "jobs.list_seen":
        return response({ jobs: previewSeenJobs(), total: previewSeenJobs().length });
      case "jobs.list_duplicates":
        return response({ jobs: previewSeenJobs(), total: previewSeenJobs().length });
      case "jobs.get_detail":
        return response(previewJobDetail(String(params.job_id ?? "preview-job-1")));
      case "applications.list":
        // Preview samples are discovery data only: no application history.
        return response({ applications: [], total: 0 });
      case "applications.get_suppressions":
        return response({ suppressions: [], total: 0 });
      case "applications.get_suppression_policy":
        return response({ policy: previewSuppressionPolicy() });
      case "applications.update_suppression_policy":
        return response({
          policy: { ...previewSuppressionPolicy(), ...(params.policy ?? {}) },
        });
      case "jobs.merge":
      case "jobs.unmerge":
        return response({ ok: true });
      case "search_profiles.list":
        return response({ profiles: previewSearchProfiles() });
      case "search_profiles.save":
        return response({
          profile_id: String(params.profile_id ?? "preview-sp"),
          name: String(params.name ?? "Preview profile"),
          candidate_profile_id: "default",
          hard_filters: (params.hard_filters as Record<string, unknown>) ?? {},
          preferences: {},
        });
      case "search_profiles.delete":
        return response({ ok: true });
      case "discovery.auto_discover":
        return response({ discovered: 0, registered: 0, crawled: 0, failed: 0 });
      case "tasks.list":
        return response({ tasks: this.tasks });
      case "resume.list_versions":
        return response({ versions: this.versions });
      case "resume.import": {
        const filePath = String(params.file_path ?? "");
        const versionId = `preview-v${this.versions.length + 1}`;
        const label = String(params.label ?? "Imported");
        this.versions = [
          previewVersion(versionId, label, true, "imported"),
          ...this.versions.map((version) => ({ ...version, is_active: false })),
        ];
        return response({
          profile_id: "default",
          version_id: versionId,
          skills: previewSkills(),
          sections: ["summary", "experience", "skills", "education"],
          contact: { email: "candidate@example.com", phone: "+1 555 0100", location: "Remote" },
          years_experience: 7,
          source_file: filePath,
        });
      }
      case "resume.get_profile":
        return response({
          profile: {
            id: "default",
            active_version_id: this.versions.find((version) => version.is_active)?.id ?? null,
            overrides: this.overrides,
          },
        });
      case "resume.set_active": {
        this.versions = this.versions.map((version) => ({
          ...version,
          is_active: version.id === params.version_id,
        }));
        return response({ ok: true });
      }
      case "resume.update_overrides":
        this.overrides = { ...this.overrides, ...((params.overrides as Record<string, unknown>) ?? {}) };
        return response({ ok: true });
      case "resume.parse":
        return response({
          version_id: params.resume_version_id,
          text: "Sample resume text",
          sections: { summary: "Engineer", skills: "Python, SQL" },
          skills: previewSkills(),
          years_experience: 7,
        });
      case "resume.set_skill_status":
        return response({ ok: true });
      case "keywords.extract": {
        const text = String(params.text ?? "").toLowerCase();
        const known = ["python", "sql", "react", "typescript", "aws", "docker"];
        return response({ keywords: known.filter((skill) => text.includes(skill)) });
      }
      case "matching.run":
        this.matchResults = previewMatchResults();
        return response({ results: this.matchResults });
      case "matching.get_results":
        return response({ results: this.matchResults, weights: this.weights });
      case "matching.update_weights": {
        const requested = (params.weights as Record<string, number>) ?? {};
        const merged = { ...this.weights, ...requested };
        const total = Object.values(merged).reduce((sum, value) => sum + value, 0) || 1;
        this.weights = Object.fromEntries(
          Object.entries(merged).map(([key, value]) => [key, value / total]),
        );
        return response({ ok: true, weights: this.weights });
      }
      case "tasks.cancel":
        this.tasks = this.tasks.map((task) =>
          task.id === params.task_id ? { ...task, status: "cancelled" } : task,
        );
        return response({ ok: true });
      case "diagnostics.sleep": {
        const taskId = `preview-${this.tasks.length + 1}`;
        this.tasks = [
          {
            id: taskId,
            type: "diagnostics.sleep",
            status: "running",
            priority: 5,
            params,
            progress: { step: 1, total: 2 },
            error: null,
            created_at: new Date().toISOString(),
            started_at: new Date().toISOString(),
            finished_at: null,
            parent_task_id: null,
          },
          ...this.tasks,
        ];
        this.emit({
          v: 1,
          type: "event",
          params: {},
          topic: "task.progress",
          seq: 1,
          ts: new Date().toISOString(),
          task_id: taskId,
          data: { step: 1, total: 2 },
        });
        return response({ task_id: taskId });
      }
      default:
        return {
          v: 1,
          type: "response",
          id: "preview",
          params: {},
          ok: false,
          error: {
            code: "not_found",
            message: `preview bridge does not implement ${method}`,
            retryable: false,
            details: {},
          },
        };
    }
  }

  async onEvent(handler: (event: Envelope) => void): Promise<UnlistenFn> {
    this.listeners.push(handler);
    return () => {
      this.listeners = this.listeners.filter((listener) => listener !== handler);
    };
  }

  private emit(event: Envelope) {
    for (const listener of this.listeners) listener(event);
  }
}

let previewVersionSeq = 1;

function previewVersion(
  id: string,
  label: string,
  isActive: boolean,
  variant: "sample" | "imported",
): Record<string, unknown> {
  return {
    id,
    candidate_profile_id: "default",
    resume_file_id: `preview-file-${id}`,
    label,
    parser_id: "preview",
    parser_version: "preview",
    text_hash: `preview-hash-${previewVersionSeq++}`,
    parsed_json: JSON.stringify({
      text: previewResumeText(variant),
      sections: { summary: "", experience: "", skills: "" },
      contact: { email: "candidate@example.com" },
      years_experience: 7,
    }),
    is_active: isActive,
    created_at: variant === "sample" ? "2026-01-01T09:00:00Z" : new Date().toISOString(),
    original_name: "resume.pdf",
    stored_path: null,
  };
}

function previewResumeText(variant: "sample" | "imported"): string {
  const throughput = variant === "imported" ? "4B" : "2B";
  const skills =
    variant === "imported"
      ? "python, sql, typescript, data modelling, dbt"
      : "python, sql, typescript, data modelling, stakeholder communication";
  const extra = variant === "imported" ? ["- Introduced dbt models and tests"] : [];
  return [
    "Alex Morgan",
    "alex@example.com | +1 555 0100 | Remote",
    "",
    "SUMMARY",
    "Data engineer with 7 years building pipelines and analytics platforms.",
    "",
    "EXPERIENCE",
    "Senior Data Engineer — Northwind Analytics",
    "Jan 2021 - Present",
    `- Built streaming ingestion handling ${throughput} events per day`,
    "- Led migration from nightly batch to incremental jobs",
    ...extra,
    "",
    "Data Engineer — Contoso Retail",
    "Mar 2018 - Dec 2020",
    "- Modelled warehouse marts for 40 analysts",
    "",
    "SKILLS",
    skills,
    "",
    "EDUCATION",
    "BSc Computer Science — State University, 2017",
  ].join("\n");
}

function previewSkills(): string[] {
  return ["python", "sql", "typescript", "data modelling", "stakeholder communication"];
}

function previewMatchResults(): Array<Record<string, unknown>> {
  const dimension = (name: string, score: number | null, weight: number) => ({
    dimension: name,
    score,
    available: score !== null,
    weight,
    details: { summary: name, reasons: score === null ? ["no data"] : ["sample reason"] },
  });
  return [
    {
      result_id: "preview-r1",
      job_id: "preview-job-1",
      job_title: "Backend Engineer",
      company_name: "Acme",
      fit_score: 0.78,
      dimension_coverage: 0.9,
      dimensions: [
        dimension("skills_resume", 0.82, 0.35),
        dimension("experience_seniority", 0.7, 0.2),
        dimension("responsibilities", 0.65, 0.2),
        dimension("location_workplace", 1, 0.1),
        dimension("compensation", null, 0.1),
        dimension("freshness", 0.9, 0.05),
      ],
      explanations: [
        { reason_code: "skills.missing", message: "missing required: kubernetes" },
        { reason_code: "dimension.unavailable", message: "compensation: no data, excluded" },
      ],
    },
    {
      result_id: "preview-r2",
      job_id: "preview-job-2",
      job_title: "Platform Engineer",
      company_name: "Beta",
      fit_score: 0.61,
      dimension_coverage: 0.8,
      dimensions: [dimension("skills_resume", 0.6, 0.35)],
      explanations: [],
    },
    {
      result_id: "preview-r3",
      job_id: "preview-job-3",
      job_title: "Data Engineer",
      company_name: "Gamma",
      fit_score: 0.42,
      dimension_coverage: 0.8,
      dimensions: [dimension("skills_resume", 0.4, 0.35)],
      explanations: [],
    },
  ];
}

function previewSuppressionPolicy(): Record<string, unknown> {
  return {
    exact_repost_suppress: true,
    similar_role_cooldown_days: 180,
    similar_role_threshold: 0.75,
    company_wide_cooldown_enabled: false,
    company_wide_cooldown_days: 90,
  };
}

function previewJobs(): Array<Record<string, unknown>> {
  return [
    {
      job_id: "preview-job-1",
      title: "Backend Engineer",
      company_id: "preview-co-acme",
      canonical_url: "https://boards.greenhouse.io/acme/jobs/1",
      posting_status: "active",
      source_id: "preview-src-1",
      provenance: ["extract.jsonld", "extract.static_html"],
      via_index: true,
    },
    {
      job_id: "preview-job-2",
      title: "Platform Engineer",
      company_id: "preview-co-beta",
      canonical_url: "https://jobs.lever.co/beta/2",
      posting_status: "active",
      source_id: "preview-src-2",
      provenance: ["extract.public_endpoint"],
      via_index: false,
    },
    {
      job_id: "preview-job-3",
      title: "Data Engineer",
      company_id: "preview-co-gamma",
      canonical_url: "https://jobs.ashbyhq.com/gamma/3",
      posting_status: "active",
      source_id: "preview-src-3",
      provenance: ["extract.embedded_json"],
      via_index: false,
    },
  ];
}

function previewSeenJobs(): Array<Record<string, unknown>> {
  return [
    {
      job_id: "preview-job-4",
      title: "Senior Data Engineer",
      company_id: "preview-co-acme",
      canonical_url: "https://boards.greenhouse.io/acme/jobs/4",
      posting_status: "removed",
      source_id: "preview-src-1",
      provenance: ["extract.jsonld"],
      via_index: false,
      reason: "posting_removed",
      related_job_ids: [],
    },
    {
      job_id: "preview-job-5",
      title: "Backend Engineer (repost)",
      company_id: "preview-co-acme",
      canonical_url: "https://boards.greenhouse.io/acme/jobs/5",
      posting_status: "active",
      source_id: "preview-src-1",
      provenance: ["extract.jsonld"],
      via_index: true,
      reason: "duplicate",
      related_job_ids: ["preview-job-1"],
    },
  ];
}

function previewJobDetail(jobId: string): Record<string, unknown> {
  const job = previewJobs().find((entry) => entry.job_id === jobId) ?? previewJobs()[0];
  return {
    job: {
      ...job,
      posted_at: "2026-11-01T00:00:00Z",
      first_seen_at: "2026-11-02T09:00:00Z",
      last_seen_at: "2026-11-21T08:00:00Z",
      description_text: "Build reliable services with Python and Postgres.",
    },
    snapshots: [
      { content_hash: "preview-hash-2", created_at: "2026-11-10T09:00:00Z" },
      { content_hash: "preview-hash-1", created_at: "2026-11-02T09:00:00Z" },
    ],
    observations: [
      { observed_at: "2026-11-21T08:00:00Z", posting_status: "active" },
      { observed_at: "2026-11-10T09:00:00Z", posting_status: "active" },
    ],
    relations: [
      { relation: "similar", related_job_id: "preview-job-2", confidence: 0.72 },
    ],
    reposts: [
      {
        class: "reposted_identical",
        confidence: 0.96,
        material: 0,
        posted_date_reset: 1,
      },
    ],
  };
}

function previewSearchProfiles(): Array<Record<string, unknown>> {
  return [
    {
      profile_id: "preview-sp",
      name: "Backend roles",
      candidate_profile_id: "default",
      ranking_policy: "deprioritize",
      hard_filters: { title_include: "engineer", workplace_types: ["remote"] },
      preferences: {},
    },
  ];
}

function previewPlugins(): Array<Record<string, unknown>> {
  return [
    {
      plugin_id: "extract.demo",
      kind: "extraction",
      version: "1.0.0",
      api_version: "1",
      enabled: true,
      health: { state: "closed", consecutive_failures: 0 },
      capabilities: ["extract"],
      network_hosts: [],
      description: "Builtin demo extraction strategy (M0 smoke test; no real extraction).",
    },
  ];
}

function previewConfig(): Record<string, unknown> {
  return {
    app_version: "0.0.0-preview",
    protocol_version: 1,
    paths: { data_dir: "Preview mode (no Tauri bridge)", db_name: "lodestar.sqlite" },
    features: {
      search_engine_discovery: false,
      rendered_dom_extraction: false,
      local_embeddings: false,
      autofill: false,
      outcome_analytics: false,
      external_plugins: false,
    },
    logging: { level: "info", rotate_files: 10, rotate_mb: 5 },
  };
}

export const defaultBridge: WorkerBridge = isTauriRuntime()
  ? new TauriBridge()
  : new PreviewBridge();

export { PreviewBridge, TauriBridge, isTauriRuntime };
