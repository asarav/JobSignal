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
const PREVIEW_SAVED_KEY = "jobsignal.preview.saved.v1";
const PREVIEW_APPLICATIONS_KEY = "jobsignal.preview.applications.v1";
const PREVIEW_POLICIES = new Set([
  "show_all",
  "deprioritize",
  "hide_high_risk",
  "require_evidence",
]);

function loadPreviewSaved(): string[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(PREVIEW_SAVED_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed)
      ? parsed.filter((value): value is string => typeof value === "string")
      : [];
  } catch {
    return [];
  }
}

function persistPreviewSaved(saved: Set<string>): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PREVIEW_SAVED_KEY, JSON.stringify([...saved]));
  } catch {
    // A blocked storage area only costs persistence, not the session.
  }
}

interface PreviewApplicationRow {
  application_id: string;
  job_id: string;
  status: string;
  created_at: string;
  updated_at: string;
  events?: Array<Record<string, unknown>>;
  outcomes?: Array<Record<string, unknown>>;
}

function loadPreviewApplications(): PreviewApplicationRow[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(PREVIEW_APPLICATIONS_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as PreviewApplicationRow[]) : [];
  } catch {
    return [];
  }
}

function persistPreviewApplications(rows: PreviewApplicationRow[]): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(PREVIEW_APPLICATIONS_KEY, JSON.stringify(rows));
  } catch {
    // A blocked storage area only costs persistence, not the session.
  }
}

function previewApplicationSummary(
  rows: PreviewApplicationRow[],
): Record<string, number> {
  const activeStatuses = new Set(["applied", "acknowledged", "recruiter_contacted"]);
  const closedStatuses = new Set(["rejected", "withdrawn"]);
  const offerStatuses = new Set(["offered", "accepted"]);
  const now = Date.now();
  const cutOff = now - 14 * 86_400_000;
  const recent = now - 7 * 86_400_000;
  const summary = {
    total: rows.length,
    applied: rows.length,
    active: 0,
    interviewing: 0,
    offers: 0,
    closed: 0,
    follow_up: 0,
    applied_last_7_days: 0,
    responded: 0,
    responded_last_7_days: 0,
  };
  const responders = new Set<string>();
  for (const row of rows) {
    const at = new Date(row.created_at).getTime();
    if (at >= recent) summary.applied_last_7_days += 1;
    if (activeStatuses.has(row.status)) {
      summary.active += 1;
      if (at <= cutOff) summary.follow_up += 1;
    } else if (row.status === "interviewing") {
      summary.interviewing += 1;
    } else if (offerStatuses.has(row.status)) {
      summary.offers += 1;
    } else if (closedStatuses.has(row.status)) {
      summary.closed += 1;
    }
    if ((row.outcomes ?? []).some((outcome) => new Date(String(outcome.occurred_at)).getTime() >= recent)) {
      summary.responded_last_7_days += 1;
    }
    if ((row.outcomes ?? []).length > 0) responders.add(row.application_id);
  }
  summary.responded = responders.size;
  return summary;
}

function previewApplicationInfo(
  row: PreviewApplicationRow,
): Record<string, unknown> {
  const job = previewJobs().find((candidate) => candidate.job_id === row.job_id);
  return {
    ...row,
    company_id: String(job?.company_id ?? ""),
    company_name: String(job?.company_name ?? ""),
    job_title: String(job?.title ?? row.job_id),
    job_title_family: null,
    applied_at: row.created_at,
    resume_version_id: null,
    notes: "",
    events: row.events ?? [],
    outcomes: row.outcomes ?? [],
    suppressions: [],
  };
}

/** Mirror of `credibility_factor` in app/domain/services/ranking.py. */
function previewCredibilityFactor(
  risk: number | null,
  confidence: number | null,
): number {
  if (risk === null) return 1;
  const conf = confidence ?? 0;
  return Math.max(0, 1 - 0.5 * risk * Math.min(1, conf / 0.5));
}

/** Mirror of `rank_score`: only ``deprioritize`` reorders by credibility. */
function previewRankScore(
  fit: number | null,
  risk: number | null,
  confidence: number | null,
  policy: string,
): number | null {
  if (fit === null) return null;
  if (policy !== "deprioritize") return fit;
  return fit * previewCredibilityFactor(risk, confidence);
}

function previewVisibility(
  riskClass: string | null,
  confidence: number | null,
  policy: string,
): "shown" | "hidden" {
  if (!PREVIEW_POLICIES.has(policy) || riskClass === null) return "shown";
  if (policy === "show_all" || policy === "deprioritize") return "shown";
  const conf = confidence ?? 0;
  if (policy === "hide_high_risk") {
    return riskClass === "likely_inactive" && conf >= 0.5 ? "hidden" : "shown";
  }
  return riskClass === "likely_active" ? "shown" : "hidden";
}

/** Mirror of `explain` in app/domain/services/ranking.py. */
function previewRankReasons(input: {
  fit: number | null;
  risk: number | null;
  confidence: number | null;
  riskLabel: string | null;
  policy: string;
  visibilityState: "shown" | "hidden";
}): string[] {
  const reasons: string[] = [];
  if (input.fit === null) {
    reasons.push(
      "No fit score yet for this profile, so this row is ordered by recency.",
    );
  } else {
    reasons.push(`Fit ${input.fit.toFixed(2)} (heuristic), weighted by your profile.`);
  }
  if (input.risk === null) {
    reasons.push("Not assessed for posting risk yet.");
  } else {
    const evidence = `confidence ${(input.confidence ?? 0).toFixed(2)}`;
    const label = input.riskLabel ?? "posting risk";
    reasons.push(`${label}: risk ${input.risk.toFixed(2)}, ${evidence}.`);
    if (input.policy === "deprioritize" && input.visibilityState === "shown") {
      const factor = previewCredibilityFactor(input.risk, input.confidence);
      if (factor < 1) {
        reasons.push(
          `Moved down by the credibility policy (factor ${factor.toFixed(2)}); it is not hidden.`,
        );
      }
    }
  }
  if (input.visibilityState === "hidden") {
    reasons.push(
      "Hidden by the active credibility policy; reachable with the toggle.",
    );
  }
  return reasons;
}

class PreviewBridge implements WorkerBridge {
  readonly isPreview = true;
  private tasks: Array<Record<string, unknown>> = [];
  private listeners: Array<(event: Envelope) => void> = [];
  private versions: Array<Record<string, unknown>> = [
    previewVersion("preview-v1", "Sample resume (preview)", true, "sample"),
  ];
  private matchResults: Array<Record<string, unknown>> = [];
  private savedJobs = new Set<string>(loadPreviewSaved());
  private applications = loadPreviewApplications();
  private mergedCompanies: Record<string, string> = {};
  private mergeRecords: Array<Record<string, unknown>> = [];
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
        return response({ sources: previewSources() });
      case "sources.add": {
        const url = String(params.url ?? "");
        const source = {
          source_id: `preview-src-added-${previewAddedSources.length + 1}`,
          url,
          kind: "preview",
          enabled: true,
          interval_s: Number(params.interval_s ?? 3600),
          tier: "cold",
          tier_reason: "Preview source, not crawled over the network",
        };
        previewAddedSources.push(source);
        return response(source);
      }
      case "discovery.run": {
        // Preview mode never fetches the network, so the crawl is a no-op with
        // worker-shaped counters rather than a fabricated harvest.
        return response({
          discovered: 0,
          new: 0,
          updated: 0,
          skipped: 0,
          removed: 0,
          errors: 0,
          gone: 0,
        });
      }
      case "crawl_runs.list":
        return response({ runs: previewCrawlRuns() });
      case "jobs.list":
      case "jobs.search": {
        const jobs = this.jobSummaries().filter((job) => previewMatchesFilters(job, params));
        const offset = Number(params.offset ?? 0);
        const limit = Number(params.limit ?? 50);
        return response({
          jobs: jobs.slice(offset, offset + limit),
          total: jobs.length,
        });
      }
      case "jobs.ranked": {
        const policy = String(params.ranking_policy ?? "deprioritize");
        const all = this.rankedJobSummaries(policy).filter((job) =>
          previewMatchesFilters(job, params),
        );
        const hiddenCount = all.filter((job) => job.hidden_by_policy === true).length;
        let visible = all.filter(
          (job) => params.include_hidden === true || job.hidden_by_policy !== true,
        );
        const minFit = typeof params.min_fit === "number" ? params.min_fit : null;
        if (minFit !== null) {
          visible = visible.filter(
            (job) => typeof job.fit_score === "number" && job.fit_score >= minFit,
          );
        }
        const riskClasses = Array.isArray(params.risk_classes)
          ? params.risk_classes.map(String)
          : [];
        if (riskClasses.length > 0) {
          visible = visible.filter((job) =>
            riskClasses.includes(String(job.risk_class ?? "")),
          );
        }
        visible.sort((left, right) => {
          const leftScore = typeof left.rank_score === "number" ? left.rank_score : null;
          const rightScore = typeof right.rank_score === "number" ? right.rank_score : null;
          if (leftScore === null && rightScore !== null) return 1;
          if (rightScore === null && leftScore !== null) return -1;
          if (leftScore !== null && rightScore !== null && leftScore !== rightScore) {
            return rightScore - leftScore;
          }
          return String(left.title).localeCompare(String(right.title));
        });
        const offset = Number(params.offset ?? 0);
        const limit = Number(params.limit ?? 50);
        return response({
          jobs: visible.slice(offset, offset + limit),
          total: visible.length,
          profile_id: "default",
          policy,
          window: all.length,
          window_truncated: false,
          hidden_by_policy: hiddenCount,
          needs_attention: all.filter(
            (job) =>
              job.risk_class === "needs_verification" || job.risk_class === "likely_inactive",
          ).length,
          // Sample rows carry no first-seen timestamps, so every stored sample
          // counts as new; the number still describes the whole sample corpus.
          new_since: all.length,
        });
      }
      case "jobs.list_saved": {
        const tracked = new Set(this.applications.map((row) => row.job_id));
        let jobs = this.savedJobSummaries();
        if (String(params.view ?? "ready") === "ready") {
          jobs = jobs.filter((job) => !tracked.has(String(job.job_id)));
        }
        jobs = jobs.map((job) => ({
          ...job,
          saved: true,
          tracked: tracked.has(String(job.job_id)),
        }));
        const query = String(params.query ?? "").trim().toLowerCase();
        if (query) {
          jobs = jobs.filter(
            (job) =>
              String(job.title ?? "").toLowerCase().includes(query) ||
              String(job.company_name ?? "").toLowerCase().includes(query),
          );
        }
        const sort = String(params.sort ?? "rank");
        if (sort === "newest") {
          jobs.sort((left, right) =>
            String(right.first_seen_at ?? "").localeCompare(String(left.first_seen_at ?? "")),
          );
        } else if (sort === "company") {
          jobs.sort((left, right) =>
            String(left.company_name ?? "").localeCompare(String(right.company_name ?? "")),
          );
        } else {
          jobs.sort((left, right) => {
            const leftFit = typeof left.fit_score === "number" ? left.fit_score : -1;
            const rightFit = typeof right.fit_score === "number" ? right.fit_score : -1;
            return rightFit - leftFit;
          });
        }
        const offset = Number(params.offset ?? 0);
        const limit = Number(params.limit ?? 25);
        return response({ jobs: jobs.slice(offset, offset + limit), total: jobs.length });
      }
      case "jobs.set_user_state": {
        const jobId = String(params.job_id ?? "");
        const state = String(params.state ?? "");
        if (state === "saved") this.savedJobs.add(jobId);
        else this.savedJobs.delete(jobId);
        persistPreviewSaved(this.savedJobs);
        return response({ job_id: jobId, state, hidden: false });
      }
      case "jobs.verify_now":
        return response({
          task_id: null,
          checked: 0,
          states: {},
          job_ids: [String(params.job_id ?? "")],
        });
      case "posting_intelligence.get_signals": {
        const jobId = String(params.job_id ?? "");
        const job = previewJobs().find((row) => row.job_id === jobId && row.risk_class);
        const assessment = job
          ? {
              class: job.risk_class,
              risk: job.risk,
              confidence: job.risk_confidence,
              evidence_sufficiency: "limited",
              evaluated_at: "2026-11-21T08:00:00Z",
              model_version: "risk.heuristic_v1",
            }
          : null;
        return response({
          job_id: jobId,
          assessment,
          signals: job
            ? [
                { signal_id: "age_freshness", status: "present", risk: 0.02, missing_reason: null },
                { signal_id: "endpoint_state", status: "missing", risk: null, missing_reason: "no endpoint check yet" },
              ]
            : [],
        });
      }
      case "posting_intelligence.get_risk_history":
        return response({ job_id: String(params.job_id ?? ""), changes: [] });
      case "companies.list": {
        const companies = previewCompanyList(this.applications).filter(
          (row) => !this.mergedCompanies[String(row.company_id)],
        );
        return response({ companies, total: companies.length });
      }
      case "companies.get_detail": {
        const requestedId = String(params.company_id ?? "");
        const survivorId = this.mergedCompanies[requestedId] ?? requestedId;
        const detail = previewCompanyDetail(survivorId, this.applications);
        if (!detail) {
          return {
            v: 1,
            type: "response",
            id: "preview",
            params: {},
            ok: false,
            error: {
              code: "not_found",
              message: `No company ${requestedId} in preview data.`,
              retryable: false,
              details: {},
            },
          };
        }
        return response({
          ...detail,
          requested_id: requestedId === survivorId ? null : requestedId,
          merged_ids:
            requestedId === survivorId
              ? Object.entries(this.mergedCompanies)
                  .filter(([, survivor]) => survivor === survivorId)
                  .map(([merged]) => merged)
              : [],
        });
      }
      case "companies.get_merge_history": {
        const companyId = String(params.company_id ?? "");
        const records = companyId
          ? this.mergeRecords.filter(
              (row) => row.merged_id === companyId || row.survivor_id === companyId,
            )
          : this.mergeRecords;
        return response({
          company_id: companyId || null,
          records,
          currently_merged: { ...this.mergedCompanies },
          total: records.length,
        });
      }
      case "companies.merge": {
        const companyId = String(params.company_id ?? "");
        const intoId = String(params.into_id ?? "");
        if (!companyId || !intoId || companyId === intoId) {
          return {
            v: 1,
            type: "response",
            id: "preview",
            params: {},
            ok: false,
            error: {
              code: "validation.invalid_params",
              message: "Choose two different companies to merge.",
              retryable: false,
              details: {},
            },
          };
        }
        const previous = this.mergedCompanies[companyId] ?? null;
        const companies = previewCompanyList(this.applications);
        const mergedName =
          companies.find((row) => row.company_id === companyId)?.name ?? companyId;
        const survivorName =
          companies.find((row) => row.company_id === intoId)?.name ?? intoId;
        this.mergedCompanies[companyId] = intoId;
        this.mergeRecords = [
          ...this.mergeRecords,
          {
            record_id: `preview-merge-${this.mergeRecords.length + 1}`,
            action: "merge",
            merged_id: companyId,
            merged_name: mergedName,
            survivor_id: intoId,
            survivor_name: survivorName,
            reason: String(params.reason ?? ""),
            created_by: "preview",
            created_at: new Date().toISOString(),
          },
        ];
        return response({
          company_id: companyId,
          survivor_id: intoId,
          action: "merge",
          changed: previous !== intoId,
          previous_survivor_id: previous,
        });
      }
      case "companies.unmerge": {
        const companyId = String(params.company_id ?? "");
        const previous = this.mergedCompanies[companyId] ?? null;
        const companies = previewCompanyList(this.applications);
        const mergedName =
          companies.find((row) => row.company_id === companyId)?.name ?? companyId;
        const survivorName = previous
          ? companies.find((row) => row.company_id === previous)?.name ?? previous
          : null;
        delete this.mergedCompanies[companyId];
        if (previous) {
          this.mergeRecords = [
            ...this.mergeRecords,
            {
              record_id: `preview-merge-${this.mergeRecords.length + 1}`,
              action: "unmerge",
              merged_id: companyId,
              merged_name: mergedName,
              survivor_id: previous,
              survivor_name: survivorName,
              reason: "",
              created_by: "preview",
              created_at: new Date().toISOString(),
            },
          ];
        }
        return response({
          company_id: companyId,
          survivor_id: previous ?? companyId,
          action: "unmerge",
          changed: previous !== null,
          previous_survivor_id: previous,
        });
      }
      case "jobs.list_seen":
        return response({ jobs: previewSeenJobs(), total: previewSeenJobs().length });
      case "jobs.list_duplicates":
        return response({ jobs: previewSeenJobs(), total: previewSeenJobs().length });
      case "jobs.get_detail":
        return response(previewJobDetail(String(params.job_id ?? "preview-job-1")));
      case "applications.list": {
        const view = String(params.view ?? "all");
        const activeStatuses = ["applied", "acknowledged", "recruiter_contacted"];
        const groups: Record<string, string[] | null> = {
          all: null,
          active: activeStatuses,
          interviewing: ["interviewing"],
          offers: ["offered", "accepted"],
          closed: ["rejected", "withdrawn"],
          follow_up: activeStatuses,
        };
        let rows =
          groups[view] === null
            ? this.applications
            : this.applications.filter((row) =>
                (groups[view] ?? []).includes(row.status),
              );
        if (view === "follow_up") {
          const cutOff = Date.now() - 14 * 86_400_000;
          rows = rows.filter(
            (row) => new Date(row.created_at).getTime() <= cutOff,
          );
        }
        const query = String(params.query ?? "").trim().toLowerCase();
        if (query) {
          rows = rows.filter((row) => {
            const info = previewApplicationInfo(row);
            return (
              String(info.job_title).toLowerCase().includes(query) ||
              String(info.company_name).toLowerCase().includes(query)
            );
          });
        }
        const sort = String(params.sort ?? "newest");
        rows = [...rows].sort((left, right) => {
          if (sort === "oldest") return left.created_at.localeCompare(right.created_at);
          if (sort === "updated") return right.updated_at.localeCompare(left.updated_at);
          return right.created_at.localeCompare(left.created_at);
        });
        const total = rows.length;
        const offset = Number(params.offset ?? 0);
        const limit = Number(params.limit ?? 25);
        const applications = rows
          .slice(offset, offset + limit)
          .map(previewApplicationInfo);
        return response({
          applications,
          total,
          summary: previewApplicationSummary(this.applications),
        });
      }
      case "applications.record_outcome": {
        const applicationId = String(params.application_id ?? "");
        const kind = String(params.kind ?? "");
        this.applications = this.applications.map((row) =>
          row.application_id === applicationId
            ? {
                ...row,
                outcomes: [
                  ...(row.outcomes ?? []),
                  {
                    kind,
                    occurred_at: String(params.occurred_at ?? new Date().toISOString()),
                    note: String(params.note ?? ""),
                  },
                ],
                updated_at: new Date().toISOString(),
              }
            : row,
        );
        persistPreviewApplications(this.applications);
        return response({ ok: true });
      }
      case "applications.get_analytics": {
        const applications = this.applications.length;
        const responded = this.applications.filter((row) => (row.outcomes ?? []).length > 0)
          .length;
        return response({
          generated_at: new Date().toISOString(),
          window_days: Number(params.days ?? 365),
          applications,
          responded,
          overall: {
            response_rate: applications ? responded / applications : 0,
          },
          min_sample: 5,
          groups: [],
          note: "Preview mode uses the applications saved in this browser.",
        });
      }
      case "applications.create": {
        const jobId = String(params.job_id ?? "");
        const now = new Date().toISOString();
        const row: PreviewApplicationRow = {
          application_id: `preview-app-${this.applications.length + 1}`,
          job_id: jobId,
          status: String(params.status ?? "applied"),
          created_at: now,
          updated_at: now,
        };
        this.applications = [...this.applications, row];
        persistPreviewApplications(this.applications);
        return response(previewApplicationInfo(row));
      }
      case "applications.update_status": {
        const applicationId = String(params.application_id ?? "");
        this.applications = this.applications.map((row) =>
          row.application_id === applicationId
            ? {
                ...row,
                status: String(params.status ?? row.status),
                updated_at: new Date().toISOString(),
              }
            : row,
        );
        persistPreviewApplications(this.applications);
        const updated = this.applications.find(
          (row) => row.application_id === applicationId,
        );
        return response(
          updated
            ? previewApplicationInfo(updated)
            : { application_id: applicationId },
        );
      }
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
      case "system.get_backup_history":
        return response({
          runs: [],
          note: "Preview mode keeps no backup history.",
        });
      case "system.export_backup":
        return response({
          ok: true,
          path: "backups/preview-backup.json",
          checksum: "preview",
          counts: {},
          schema_version: 18,
          created_at: "2026-01-01T00:00:00Z",
        });
      case "system.import_backup":
        return {
          v: 1,
          type: "response",
          id: "preview",
          params: {},
          ok: false,
          error: {
            code: "plugin.unavailable",
            message: "Preview mode cannot restore a backup.",
            retryable: false,
            details: {},
          },
        };
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

  private jobSummaries(): Array<Record<string, unknown>> {
    return previewJobs().map((job) => ({
      ...job,
      saved: this.savedJobs.has(String(job.job_id)),
    }));
  }

  private savedJobSummaries(): Array<Record<string, unknown>> {
    return this.jobSummaries().filter((job) => job.saved === true);
  }

  /** Fit and risk stay separate; the rank score follows the approved policy. */
  private rankedJobSummaries(policy: string): Array<Record<string, unknown>> {
    return this.jobSummaries().map((job) => {
      const fit = typeof job.fit_score === "number" ? job.fit_score : null;
      const risk = typeof job.risk === "number" ? job.risk : null;
      const confidence =
        typeof job.risk_confidence === "number" ? job.risk_confidence : null;
      const riskClass = typeof job.risk_class === "string" ? job.risk_class : null;
      const riskLabel = typeof job.risk_label === "string" ? job.risk_label : null;
      const visibilityState = previewVisibility(riskClass, confidence, policy);
      return {
        ...job,
        hidden_by_policy: visibilityState === "hidden",
        rank_score: previewRankScore(fit, risk, confidence, policy),
        rank_reasons: previewRankReasons({
          fit,
          risk,
          confidence,
          riskLabel,
          policy,
          visibilityState,
        }),
      };
    });
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

function previewCompanyList(
  applications: PreviewApplicationRow[] = [],
): Array<Record<string, unknown>> {
  const companies = new Map<
    string,
    {
      company_id: string;
      name: string;
      domain: string | null;
      jobIds: Set<string>;
      job_count: number;
      active_job_count: number;
    }
  >();
  for (const job of previewJobs()) {
    const companyId = String(job.company_id ?? "");
    if (!companyId) continue;
    const entry = companies.get(companyId) ?? {
      company_id: companyId,
      name: String(job.company_name ?? companyId),
      domain: null,
      jobIds: new Set<string>(),
      job_count: 0,
      active_job_count: 0,
    };
    entry.jobIds.add(String(job.job_id));
    entry.job_count += 1;
    if (job.posting_status === "active") entry.active_job_count += 1;
    if (!entry.domain && typeof job.canonical_url === "string") {
      try {
        entry.domain = new URL(job.canonical_url).hostname;
      } catch {
        entry.domain = null;
      }
    }
    companies.set(companyId, entry);
  }
  return [...companies.values()]
    .map((entry) => ({
      company_id: entry.company_id,
      name: entry.name,
      domain: entry.domain,
      job_count: entry.job_count,
      active_job_count: entry.active_job_count,
      application_count: applications.filter((row) => entry.jobIds.has(row.job_id)).length,
      last_seen_at: "2026-11-21T02:00:00Z",
      merged_ids: [],
      merged_company_count: 0,
    }))
    .sort((left, right) => String(left.name).localeCompare(String(right.name)));
}

function previewCompanyDetail(
  companyId: string,
  applications: PreviewApplicationRow[] = [],
): Record<string, unknown> | null {
  const company = previewCompanyList(applications).find(
    (row) => row.company_id === companyId,
  );
  if (!company) return null;
  const jobs = previewJobs().filter((job) => job.company_id === companyId);
  const jobIds = new Set(jobs.map((job) => job.job_id));
  return {
    company_id: company.company_id,
    name: company.name,
    domain: company.domain,
    requested_id: null,
    merged_ids: [],
    jobs,
    job_count: company.job_count,
    active_job_count: company.active_job_count,
    repost_count: 0,
    summary: {
      observed_postings: jobs.length,
      active_postings: jobs.filter((job) => job.posting_status === "active").length,
      applications: applications.filter((row) => jobIds.has(row.job_id)).length,
    },
    applications: applications.filter((row) => jobIds.has(row.job_id)),
    recent_window_days: 30,
    window_start: "2026-10-22T00:00:00Z",
  };
}

function previewJobs(): Array<Record<string, unknown>> {
  return [
    {
      job_id: "preview-job-1",
      title: "Backend Engineer",
      company_id: "preview-co-acme",
      company_name: "Acme",
      location_text: "Berlin, Germany",
      workplace_type: "remote",
      salary_min: 80000,
      salary_max: 105000,
      salary_currency: "EUR",
      fit_score: 0.78,
      risk: 0.05,
      risk_confidence: 0.82,
      risk_class: "likely_active",
      risk_label: "Recent activity, consistent description",
      credibility: 0.95,
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
      company_name: "Beta",
      location_text: "London, UK",
      workplace_type: "hybrid",
      // Deliberately unassessed: no fit or risk may be shown as zero.
      fit_score: null,
      risk: null,
      risk_confidence: null,
      risk_class: null,
      risk_label: null,
      credibility: null,
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
      company_name: "Gamma",
      location_text: "Remote (EU)",
      workplace_type: "remote",
      salary_min: null,
      salary_max: null,
      salary_currency: null,
      fit_score: 0.42,
      risk: 0.82,
      risk_confidence: 0.7,
      risk_class: "likely_inactive",
      risk_label: "Application page returned a closed message",
      credibility: 0.18,
      canonical_url: "https://jobs.ashbyhq.com/gamma/3",
      posting_status: "active",
      source_id: "preview-src-3",
      provenance: ["extract.embedded_json"],
      via_index: false,
    },
    // Enough extra rows that the eight-row dashboard page is a slice of the
    // whole set, so the headline aggregate cannot be mistaken for a page count.
    ...Array.from({ length: 8 }, (_, index) => ({
      job_id: `preview-job-${index + 4}`,
      title: `Sample Engineer ${index + 1}`,
      company_id: `preview-co-${index + 4}`,
      company_name: `Sample Co ${index + 1}`,
      location_text: "Remote (EU)",
      workplace_type: "remote",
      salary_min: null,
      salary_max: null,
      salary_currency: null,
      fit_score: 0.5 - index * 0.02,
      risk: 0.02,
      risk_confidence: 0.6,
      risk_class: "likely_active",
      risk_label: "Recent activity",
      credibility: 0.98,
      canonical_url: `https://example.test/jobs/${index + 4}`,
      posting_status: "active",
      source_id: "preview-src-1",
      provenance: ["extract.jsonld"],
      via_index: false,
    })),
    {
      // Ranked below the eight-row page, so the whole-corpus count must still
      // include it.
      job_id: "preview-job-12",
      title: "Legacy Systems Engineer",
      company_id: "preview-co-delta",
      company_name: "Delta",
      location_text: "Hamburg, Germany",
      workplace_type: "onsite",
      salary_min: null,
      salary_max: null,
      salary_currency: null,
      fit_score: 0.31,
      risk: 0.6,
      risk_confidence: 0.45,
      risk_class: "needs_verification",
      risk_label: "Posting age and no strong evidence either way",
      credibility: 0.4,
      canonical_url: "https://example.test/jobs/12",
      posting_status: "active",
      source_id: "preview-src-2",
      provenance: ["extract.static_html"],
      via_index: false,
    },
  ];
}

/** Mirror the worker's text and hard-filter params for preview data. */
function previewMatchesFilters(
  job: Record<string, unknown>,
  params: Record<string, unknown>,
): boolean {
  const query = String(params.query ?? "").trim().toLowerCase();
  const haystack = [job.title, job.company_name, job.location_text]
    .map((value) => String(value ?? ""))
    .join(" ")
    .toLowerCase();
  if (query && !haystack.includes(query)) return false;

  const filters = (params.filters ?? {}) as Record<string, unknown>;
  const firstSeenAfter = filters.first_seen_after;
  if (
    typeof firstSeenAfter === "string" &&
    firstSeenAfter &&
    String(job.first_seen_at ?? new Date().toISOString()) <= firstSeenAfter
  ) {
    return false;
  }
  const title = filters.title_include;
  if (typeof title === "string" && title && !String(job.title).toLowerCase().includes(title.toLowerCase())) {
    return false;
  }
  const location = filters.location;
  if (
    typeof location === "string" &&
    location &&
    !String(job.location_text ?? "").toLowerCase().includes(location.toLowerCase())
  ) {
    return false;
  }
  const types = filters.workplace_types;
  if (Array.isArray(types) && types.length > 0 && !types.includes(job.workplace_type)) {
    return false;
  }
  return true;
}

const previewAddedSources: Array<Record<string, unknown>> = [];

function previewSources(): Array<Record<string, unknown>> {
  return [
    ...previewAddedSources,
    {
      source_id: "preview-src-1",
      url: "https://boards.greenhouse.io/acme",
      kind: "ats",
      enabled: true,
      tier: "hot",
      tier_reason: "Crawl succeeded 12 times",
    },
    {
      source_id: "preview-src-2",
      url: "https://jobs.lever.co/beta",
      kind: "ats",
      enabled: true,
      tier: "warm",
      tier_reason: "One crawl error",
    },
  ];
}

function previewCrawlRuns(): Array<Record<string, unknown>> {
  return [
    {
      run_id: "preview-run-1",
      source_id: "preview-src-1",
      status: "completed",
      started_at: "2026-11-21T07:55:00Z",
      finished_at: "2026-11-21T08:00:00Z",
      stats: { discovered: 12, new: 4 },
      error: null,
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
