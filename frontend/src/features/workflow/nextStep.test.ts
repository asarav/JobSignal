import { describe, expect, it } from "vitest";
import { nextStep, stageStatus, type WorkflowState } from "./nextStep";

function state(overrides: Partial<WorkflowState> = {}): WorkflowState {
  return {
    hasResume: true,
    hasSources: true,
    hasMatches: true,
    matchCount: 12,
    savedCount: 0,
    appliedCount: 0,
    lastCrawl: null,
    newSince: 0,
    readyToApply: 0,
    followUps: 0,
    appliedLast7Days: 0,
    responsesLast7Days: 0,
    ...overrides,
  };
}

describe("nextStep", () => {
  it("follows the workflow order and stops at the first unfinished step", () => {
    expect(nextStep(state({ hasResume: false })).stage).toBe("profile");
    expect(nextStep(state({ hasSources: false }))).toMatchObject({
      stage: "sources",
      to: "/settings?tab=sources&from=plan",
    });
    expect(nextStep(state({ matchCount: 0, hasMatches: false }))).toMatchObject({
      stage: "review",
      cta: "Crawl a source",
    });
    expect(nextStep(state())).toMatchObject({
      stage: "review",
      title: "Review 12 matches",
      to: "/discover",
    });
    expect(nextStep(state({ savedCount: 2 }))).toMatchObject({
      stage: "apply",
      title: "Prepare your first application",
      to: "/saved?view=ready",
    });
    expect(nextStep(state({ savedCount: 2, appliedCount: 1 }))).toMatchObject({
      stage: "track",
      to: "/applications",
    });
  });

  it("prioritises ready roles, new matches and follow-ups", () => {
    expect(nextStep(state({ readyToApply: 4, newSince: 9 }))).toMatchObject({
      stage: "apply",
      title: "4 roles are ready to apply",
      to: "/saved?view=ready",
    });
    expect(nextStep(state({ newSince: 9 }))).toMatchObject({
      stage: "review",
      title: "Review 9 new matches",
      to: "/discover?new=1",
    });
    expect(nextStep(state({ followUps: 2, savedCount: 3, appliedCount: 2 }))).toMatchObject({
      stage: "track",
      title: "2 applications are waiting 14+ days",
      to: "/applications?view=follow_up",
    });
  });

  it("never asserts a count it was not given", () => {
    const step = nextStep(state({ matchCount: 0, hasMatches: false, savedCount: 0 }));
    expect(step.title).not.toMatch(/0 match/);
  });

  it("uses the last crawl, not a hardcoded explanation, when nothing matched", () => {
    const never = nextStep(state({ matchCount: 0, hasMatches: false, lastCrawl: null }));
    const ran = nextStep(
      state({ matchCount: 0, hasMatches: false, lastCrawl: "2026-11-24T08:00:00Z" }),
    );
    expect(never.reason).toContain("run a crawl");
    expect(ran.reason).toContain("2026-11-24T08:00:00Z");
    expect(ran.reason).not.toBe(never.reason);
  });
});

describe("stageStatus", () => {
  it("marks earlier completed stages done and later ones upcoming", () => {
    const current = state({ savedCount: 3 });
    expect(stageStatus(current, "profile", "apply")).toBe("done");
    expect(stageStatus(current, "shortlist", "apply")).toBe("done");
    expect(stageStatus(current, "apply", "apply")).toBe("current");
    expect(stageStatus(current, "track", "apply")).toBe("upcoming");
  });

  it("keeps later stages upcoming when counts are missing", () => {
    const fresh = state({
      hasResume: false,
      hasSources: false,
      matchCount: 0,
      hasMatches: false,
      savedCount: 0,
    });
    expect(stageStatus(fresh, "sources", "profile")).toBe("upcoming");
    expect(stageStatus(fresh, "review", "profile")).toBe("upcoming");
  });
});
