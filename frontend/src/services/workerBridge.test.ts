import { beforeEach, describe, expect, it, vi } from "vitest";
import { PreviewBridge, TauriBridge, isTauriRuntime } from "./workerBridge";

const invoke = vi.hoisted(() => vi.fn());
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn() }));

async function payload(
  bridge: PreviewBridge,
  method: string,
  params: Record<string, unknown> = {},
): Promise<Record<string, unknown>> {
  const envelope = await bridge.request(method, params);
  return envelope.result as Record<string, unknown>;
}

function rows(body: Record<string, unknown>): Array<Record<string, unknown>> {
  return (body.jobs ?? []) as Array<Record<string, unknown>>;
}

beforeEach(() => {
  window.localStorage.clear();
});

describe("PreviewBridge ranking", () => {
  it("computes fit × credibility and explains every row", async () => {
    const body = await payload(new PreviewBridge(), "jobs.ranked", {
      limit: 50,
      ranking_policy: "deprioritize",
    });
    const jobs = rows(body);

    expect(jobs).toHaveLength(12);
    const backend = jobs.find((row) => row.job_id === "preview-job-1");
    expect(backend?.rank_score).toBeCloseTo(0.78 * (1 - 0.5 * 0.05), 6);
    const reasons = ((backend?.rank_reasons as string[] | undefined) ?? []).join(" ");
    expect(reasons).toContain("Fit 0.78");
    expect(reasons).toContain("risk 0.05, confidence 0.82");
    expect(reasons).toContain("Moved down by the credibility policy");

    // The unassessed sample keeps a missing rank, never 0.
    const unassessed = jobs.find((row) => row.job_id === "preview-job-2");
    expect(unassessed?.rank_score).toBeNull();
    expect(
      ((unassessed?.rank_reasons as string[] | undefined) ?? []).join(" "),
    ).toContain(
      "No fit score yet for this profile",
    );

    // Rows are ordered by the policy score, strongest first.
    const scores = jobs
      .map((row) => row.rank_score)
      .filter((value): value is number => typeof value === "number");
    expect([...scores].sort((left, right) => right - left)).toEqual(scores);
  });

  it("reports whole-corpus headline counts beyond the eight-row page", async () => {
    const body = await payload(new PreviewBridge(), "jobs.ranked", {
      limit: 8,
      ranking_policy: "deprioritize",
    });
    const page = rows(body);

    expect(page).toHaveLength(8);
    expect(body.window).toBe(12);
    expect(body.total).toBe(12);
    expect(body.needs_attention).toBe(2);
    // Neither risky posting reaches the eight-row page, so the count cannot
    // have been derived from it.
    expect(
      page.filter(
        (row) =>
          row.risk_class === "needs_verification" || row.risk_class === "likely_inactive",
      ),
    ).toHaveLength(0);
  });

  it("uses fit alone under the hiding policies and hides high risk", async () => {
    const showAll = await payload(new PreviewBridge(), "jobs.ranked", {
      limit: 50,
      ranking_policy: "show_all",
    });
    const stranded = rows(showAll).find((row) => row.job_id === "preview-job-3");
    expect(stranded?.rank_score).toBeCloseTo(0.42, 6);

    const hide = await payload(new PreviewBridge(), "jobs.ranked", {
      limit: 50,
      ranking_policy: "hide_high_risk",
    });
    expect(hide.hidden_by_policy).toBe(1);
    expect(hide.total).toBe(11);
    expect(
      rows(hide).some((row) => row.hidden_by_policy === true),
    ).toBe(false);

    const withHidden = await payload(new PreviewBridge(), "jobs.ranked", {
      limit: 50,
      ranking_policy: "hide_high_risk",
      include_hidden: true,
    });
    expect(withHidden.total).toBe(12);
    expect(
      rows(withHidden).find((row) => row.job_id === "preview-job-3")?.hidden_by_policy,
    ).toBe(true);
  });
});

describe("PreviewBridge search, sources and applications", () => {
  it("honours the same text and hard filters as the worker", async () => {
    const bridge = new PreviewBridge();
    const byQuery = await payload(bridge, "jobs.list", { query: "Data" });
    expect(rows(byQuery).map((row) => row.job_id)).toEqual(["preview-job-3"]);

    const byFilter = await payload(bridge, "jobs.list", {
      filters: { title_include: "Engineer", workplace_types: ["hybrid"] },
    });
    expect(rows(byFilter).every((row) => row.workplace_type === "hybrid")).toBe(true);
  });

  it("adds a source and reports a worker-shaped crawl result", async () => {
    const bridge = new PreviewBridge();
    const source = await payload(bridge, "sources.add", {
      url: "https://boards.greenhouse.io/example",
      interval_s: 3600,
    });
    expect(source.url).toBe("https://boards.greenhouse.io/example");
    const sources = await payload(bridge, "sources.list");
    expect((sources.sources as unknown[]).length).toBe(3);
    expect(await payload(bridge, "discovery.run", { source_id: source.source_id })).toMatchObject({
      discovered: 0,
    });
  });

  it("persists an application created from Saved or job detail", async () => {
    const bridge = new PreviewBridge();
    await payload(bridge, "applications.create", {
      job_id: "preview-job-3",
      status: "applied",
    });

    const reloaded = new PreviewBridge();
    const listed = await payload(reloaded, "applications.list");
    const applications = listed.applications as Array<Record<string, unknown>>;
    expect(applications).toHaveLength(1);
    expect(applications[0]).toMatchObject({
      job_id: "preview-job-3",
      job_title: "Data Engineer",
      status: "applied",
    });

    await payload(reloaded, "applications.update_status", {
      application_id: applications[0].application_id,
      status: "interviewing",
    });
    const updated = await payload(new PreviewBridge(), "applications.list");
    expect(
      (updated.applications as Array<Record<string, unknown>>)[0].status,
    ).toBe("interviewing");
  });
});

describe("PreviewBridge companies", () => {
  it("lists sample companies with counts and opens a company detail", async () => {
    const bridge = new PreviewBridge();
    await payload(bridge, "applications.create", {
      job_id: "preview-job-1",
      status: "applied",
    });

    const listed = await payload(bridge, "companies.list");
    const companies = listed.companies as Array<Record<string, unknown>>;
    expect(listed.total).toBe(12);
    const acme = companies.find((row) => row.company_id === "preview-co-acme");
    expect(acme).toMatchObject({ name: "Acme", job_count: 1, application_count: 1 });

    const detail = await payload(bridge, "companies.get_detail", {
      company_id: "preview-co-gamma",
    });
    expect(detail).toMatchObject({ name: "Gamma", job_count: 1 });
    expect((detail.jobs as unknown[]).length).toBe(1);
  });

  it("records a merge, resolves the merged id and can separate it again", async () => {
    const bridge = new PreviewBridge();
    const merged = await payload(bridge, "companies.merge", {
      company_id: "preview-co-acme",
      into_id: "preview-co-gamma",
      reason: "Same employer",
    });
    expect(merged).toMatchObject({ changed: true, survivor_id: "preview-co-gamma" });

    const listed = await payload(bridge, "companies.list");
    expect(
      (listed.companies as Array<Record<string, unknown>>).some(
        (row) => row.company_id === "preview-co-acme",
      ),
    ).toBe(false);

    const resolved = await payload(bridge, "companies.get_detail", {
      company_id: "preview-co-acme",
    });
    expect(resolved).toMatchObject({
      company_id: "preview-co-gamma",
      requested_id: "preview-co-acme",
    });
    const history = await payload(bridge, "companies.get_merge_history", {
      company_id: "preview-co-acme",
    });
    expect((history.records as unknown[]).length).toBe(1);
    expect((history.records as Array<Record<string, unknown>>)[0]).toMatchObject({
      merged_name: "Acme",
      action: "merge",
      survivor_id: "preview-co-gamma",
    });

    const separated = await payload(bridge, "companies.unmerge", {
      company_id: "preview-co-acme",
    });
    expect(separated).toMatchObject({ changed: true, survivor_id: "preview-co-gamma" });
    const restored = await payload(bridge, "companies.list");
    expect(
      (restored.companies as Array<Record<string, unknown>>).some(
        (row) => row.company_id === "preview-co-acme",
      ),
    ).toBe(true);
  });
});

describe("bridge selection and preview scoping", () => {
  it("detects the packaged Tauri runtime instead of the browser preview", async () => {
    expect(isTauriRuntime()).toBe(false);
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    expect(isTauriRuntime()).toBe(true);
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;

    vi.resetModules();
    (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {};
    const desktop = await import("./workerBridge");
    expect(desktop.defaultBridge.isPreview).toBe(false);
    delete (window as unknown as Record<string, unknown>).__TAURI_INTERNALS__;
    vi.resetModules();
  });

  it("forwards preview-only methods to the desktop worker without preview state", async () => {
    invoke.mockResolvedValue({ ok: true });
    await new TauriBridge().request("sources.add", { url: "https://example.test/jobs" });
    await new TauriBridge().request("discovery.run", { source_id: "s1" });
    await new TauriBridge().request("applications.create", { job_id: "j1" });

    expect(invoke).toHaveBeenNthCalledWith(1, "worker_request", {
      method: "sources.add",
      params: { url: "https://example.test/jobs" },
    });
    expect(invoke).toHaveBeenNthCalledWith(3, "worker_request", {
      method: "applications.create",
      params: { job_id: "j1" },
    });
    expect(window.localStorage.length).toBe(0);
  });
});

describe("PreviewBridge saved state", () => {
  it("survives a reload and is the same record Discover reads", async () => {
    const beforeReload = new PreviewBridge();
    await payload(beforeReload, "jobs.set_user_state", {
      job_id: "preview-job-1",
      state: "saved",
    });

    // A new bridge instance is what a hard page load constructs.
    const afterReload = new PreviewBridge();
    const saved = await payload(afterReload, "jobs.list_saved");
    expect(rows(saved).map((row) => row.job_id)).toEqual(["preview-job-1"]);

    const ranked = await payload(afterReload, "jobs.ranked", { limit: 50 });
    expect(
      rows(ranked).find((row) => row.job_id === "preview-job-1")?.saved,
    ).toBe(true);
  });

  it("removes the bookmark when another tracking state replaces it", async () => {
    const bridge = new PreviewBridge();
    await payload(bridge, "jobs.set_user_state", {
      job_id: "preview-job-2",
      state: "saved",
    });
    await payload(bridge, "jobs.set_user_state", {
      job_id: "preview-job-2",
      state: "applied",
    });

    const reloaded = new PreviewBridge();
    expect((await payload(reloaded, "jobs.list_saved")).total).toBe(0);
  });
});
