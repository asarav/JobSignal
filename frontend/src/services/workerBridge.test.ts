import { beforeEach, describe, expect, it } from "vitest";
import { PreviewBridge } from "./workerBridge";

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
