import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { WorkflowNextStepBar } from "./WorkflowNextStepBar";
import type { WorkflowState } from "../features/workflow/nextStep";

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

function renderBar(value: WorkflowState) {
  return render(
    <MemoryRouter>
      <WorkflowNextStepBar state={value} />
    </MemoryRouter>,
  );
}

describe("WorkflowNextStepBar", () => {
  it("points an unsaved review at Saved using real counts", () => {
    renderBar(state());
    expect(screen.getByText(/12 matches to review/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review matches →" })).toHaveAttribute(
      "href",
      "/discover?new=1",
    );
  });

  it("points saved roles at applying them", () => {
    renderBar(state({ savedCount: 3, readyToApply: 3 }));
    expect(screen.getByText(/3 ready to apply · 3 on the shortlist/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open apply queue →" })).toHaveAttribute(
      "href",
      "/saved?view=ready",
    );
  });

  it("points tracked applications at recording outcomes", () => {
    renderBar(state({ savedCount: 3, appliedCount: 2 }));
    expect(screen.getByText(/2 tracked · Next: record outcomes/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review applications →" })).toHaveAttribute(
      "href",
      "/applications",
    );
  });

  it("asks for a source when setup is incomplete", () => {
    renderBar(state({ hasSources: false, hasMatches: false, matchCount: 0 }));
    expect(screen.getByText(/Add a job source/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add a source →" })).toHaveAttribute(
      "href",
      "/settings?tab=sources&from=plan",
    );
  });
});
