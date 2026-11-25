import { readFileSync } from "node:fs";
import { join } from "node:path";
import { beforeEach, describe, expect, it } from "vitest";
import { PreviewBridge } from "./workerBridge";

type JsonSchema = {
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
  type?: string | string[];
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  allOf?: JsonSchema[];
  enum?: unknown[];
  const?: unknown;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  additionalProperties?: boolean | JsonSchema;
  items?: JsonSchema;
};

type MethodEntry = {
  method: string;
  response_schema: string;
};

const frontendRoot = process.cwd().endsWith("frontend")
  ? process.cwd()
  : join(process.cwd(), "frontend");
const root = join(frontendRoot, "..");
const catalog = JSON.parse(
  readFileSync(join(root, "schemas", "ipc", "methods.json"), "utf8"),
) as { methods: MethodEntry[] };
const schemas = new Map(
  catalog.methods.map((entry) => [
    entry.method,
    JSON.parse(readFileSync(join(root, entry.response_schema), "utf8")) as JsonSchema,
  ]),
);

function matchesType(value: unknown, type: string): boolean {
  if (type === "null") return value === null;
  if (type === "array") return Array.isArray(value);
  if (type === "object") return typeof value === "object" && value !== null && !Array.isArray(value);
  if (type === "integer") return Number.isInteger(value);
  if (type === "number") return typeof value === "number";
  return typeof value === type;
}

function validate(
  schema: JsonSchema,
  value: unknown,
  rootSchema: JsonSchema,
  path: string,
  errors: string[],
): void {
  if (schema.$ref) {
    const name = schema.$ref.replace("#/$defs/", "");
    const target = rootSchema.$defs?.[name];
    if (!target) {
      errors.push(`${path}: unresolved ${schema.$ref}`);
      return;
    }
    validate(target, value, rootSchema, path, errors);
    return;
  }
  if (schema.const !== undefined && value !== schema.const) {
    errors.push(`${path}: expected const ${String(schema.const)}`);
  }
  if (schema.enum && !schema.enum.includes(value)) {
    errors.push(`${path}: ${String(value)} not in enum`);
  }
  if (schema.anyOf) {
    const candidateErrors = schema.anyOf.map((candidate) => {
      const inner: string[] = [];
      validate(candidate, value, rootSchema, path, inner);
      return inner;
    });
    if (candidateErrors.every((inner) => inner.length > 0)) {
      errors.push(`${path}: no anyOf branch matched (${String(value)})`);
    }
  }
  if (schema.oneOf) {
    const matches = schema.oneOf.filter((candidate) => {
      const inner: string[] = [];
      validate(candidate, value, rootSchema, path, inner);
      return inner.length === 0;
    });
    if (matches.length !== 1) errors.push(`${path}: expected exactly one oneOf branch`);
  }
  if (schema.allOf) {
    for (const candidate of schema.allOf) {
      validate(candidate, value, rootSchema, path, errors);
    }
  }
  if (schema.type) {
    const types = Array.isArray(schema.type) ? schema.type : [schema.type];
    if (!types.some((type) => matchesType(value, type))) {
      errors.push(`${path}: expected ${types.join("|")}, got ${typeof value}`);
      return;
    }
  }
  if (Array.isArray(value) && schema.items) {
    value.forEach((item, index) =>
      validate(schema.items as JsonSchema, item, rootSchema, `${path}[${index}]`, errors),
    );
  }
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const record = value as Record<string, unknown>;
    for (const key of schema.required ?? []) {
      if (!(key in record)) errors.push(`${path}: missing required '${key}'`);
    }
    for (const [key, property] of Object.entries(schema.properties ?? {})) {
      if (key in record) {
        validate(property, record[key], rootSchema, `${path}.${key}`, errors);
      }
    }
    if (schema.additionalProperties === false) {
      const known = new Set(Object.keys(schema.properties ?? {}));
      for (const key of Object.keys(record)) {
        if (!known.has(key)) errors.push(`${path}: unexpected '${key}'`);
      }
    } else if (typeof schema.additionalProperties === "object") {
      for (const [key, item] of Object.entries(record)) {
        if (!(key in (schema.properties ?? {}))) {
          validate(schema.additionalProperties, item, rootSchema, `${path}.${key}`, errors);
        }
      }
    }
  }
}

type Call = {
  method: string;
  params: Record<string, unknown>;
  error?: boolean;
};

function callsFor(createdApplicationId: string): Call[] {
  return [
    { method: "system.hello", params: { protocol_version: 1, ui_version: "test" } },
    { method: "system.health", params: {} },
    { method: "settings.get", params: {} },
    {
      method: "settings.update",
      params: { patch: { logging: { level: "warning" } } },
    },
    { method: "plugins.list", params: {} },
    { method: "sources.list", params: {} },
    { method: "sources.add", params: { url: "https://example.test/jobs" } },
    { method: "discovery.run", params: { source_id: "preview-src-1" } },
    { method: "discovery.auto_discover", params: {} },
    { method: "crawl_runs.list", params: { limit: 1 } },
    { method: "jobs.list", params: { filters: {}, limit: 100 } },
    { method: "jobs.search", params: { query: "engineer", limit: 10 } },
    { method: "jobs.ranked", params: { limit: 50 } },
    { method: "jobs.list_saved", params: {} },
    {
      method: "jobs.set_user_state",
      params: { job_id: "preview-job-1", state: "saved" },
    },
    { method: "jobs.verify_now", params: { job_id: "preview-job-1" } },
    {
      method: "posting_intelligence.get_signals",
      params: { job_id: "preview-job-1" },
    },
    {
      method: "posting_intelligence.get_risk_history",
      params: { job_id: "preview-job-1" },
    },
    { method: "jobs.list_seen", params: {} },
    { method: "jobs.list_duplicates", params: {} },
    { method: "jobs.get_detail", params: { job_id: "preview-job-1" } },
    { method: "jobs.merge", params: { job_id: "preview-job-1", related_job_id: "preview-job-2" } },
    { method: "jobs.unmerge", params: { job_id: "preview-job-1", related_job_id: "preview-job-2" } },
    { method: "applications.list", params: {} },
    {
      method: "applications.create",
      params: { job_id: "preview-job-1", status: "applied" },
    },
    {
      method: "applications.update_status",
      params: { application_id: createdApplicationId, status: "interviewing" },
    },
    {
      method: "applications.record_outcome",
      params: { application_id: createdApplicationId, kind: "interview" },
    },
    { method: "applications.get_analytics", params: { days: 365 } },
    { method: "applications.get_suppressions", params: {} },
    { method: "applications.get_suppression_policy", params: {} },
    { method: "applications.update_suppression_policy", params: { policy: {} } },
    { method: "companies.list", params: {} },
    { method: "companies.get_detail", params: { company_id: "preview-co-acme" } },
    { method: "companies.get_merge_history", params: {} },
    {
      method: "companies.merge",
      params: { company_id: "preview-co-acme", into_id: "preview-co-gamma" },
    },
    { method: "companies.unmerge", params: { company_id: "preview-co-acme" } },
    { method: "search_profiles.list", params: {} },
    { method: "search_profiles.save", params: { name: "Test profile" } },
    { method: "search_profiles.delete", params: { profile_id: "preview-sp" } },
    { method: "resume.list_versions", params: {} },
    { method: "resume.get_profile", params: {} },
    { method: "resume.import", params: { file_path: "/tmp/resume.pdf", label: "v2" } },
    { method: "resume.set_active", params: { version_id: "preview-v1" } },
    { method: "resume.update_overrides", params: { overrides: { name: "Alex" } } },
    { method: "resume.parse", params: { resume_version_id: "preview-v1" } },
    {
      method: "resume.set_skill_status",
      params: { keyword_id: "python", status: "have_experience" },
    },
    { method: "keywords.extract", params: { text: "Python and SQL" } },
    { method: "matching.run", params: {} },
    { method: "matching.get_results", params: {} },
    { method: "matching.update_weights", params: { weights: { skills_resume: 0.4 } } },
    { method: "tasks.list", params: {} },
    { method: "tasks.cancel", params: { task_id: "missing" } },
    { method: "diagnostics.sleep", params: { seconds: 0.1, steps: 2 } },
    { method: "system.get_backup_history", params: {} },
    { method: "system.export_backup", params: {} },
    { method: "system.import_backup", params: {}, error: true },
  ];
}

describe("Preview response schema parity", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it("validates every preview handler result against the real reply schema", async () => {
    const bridge = new PreviewBridge();
    const seen = new Set<string>();
    const errors: string[] = [];
    let createdApplicationId = "preview-app-1";

    const calls = callsFor(createdApplicationId);
    for (const call of calls) {
      seen.add(call.method);
      const envelope = await bridge.request(call.method, call.params);
      if (call.error) {
        expect(envelope.ok, `${call.method} should be an intentional error`).toBe(false);
        continue;
      }
      expect(envelope.ok, `${call.method}: ${JSON.stringify(envelope.error)}`).toBe(true);
      const schema = schemas.get(call.method);
      if (!schema) {
        errors.push(`${call.method}: no real response schema`);
        continue;
      }
      validate(schema, envelope.result, schema, call.method, errors);
      if (call.method === "applications.create") {
        const result = envelope.result as Record<string, unknown>;
        createdApplicationId = String(result.application_id);
        // Keep the remainder of the sequence on the row that now exists.
        calls[25].params.application_id = createdApplicationId;
        calls[26].params.application_id = createdApplicationId;
      }
    }

    expect(errors).toEqual([]);
    expect(seen.size).toBe(calls.length);
    expect(calls.length).toBe(56);
  });
});
