import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ResumeRoute } from "./ResumeRoute";

const mocks = vi.hoisted(() => ({
  listResumeVersions: vi.fn(),
  getMatchingResults: vi.fn(),
  getResumeProfile: vi.fn(),
}));

vi.mock("../../services/workerClient", () => ({
  workerClient: mocks,
  WorkerError: class WorkerError extends Error {
    readonly code: string;
    readonly retryable = false;
    constructor(code: string, message: string) {
      super(message);
      this.code = code;
    }
  },
}));

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listResumeVersions.mockResolvedValue({ versions: [] });
  mocks.getResumeProfile.mockResolvedValue({
    profile: { id: "default", active_version_id: null, overrides: {} },
  });
});

describe("ResumeRoute missing fit data", () => {
  it("renders an unassessed job without inventing 0.00 or 0%", async () => {
    mocks.getMatchingResults.mockResolvedValue({
      weights: {},
      results: [
        {
          result_id: "r1",
          job_id: "job_1",
          job_title: "Data Engineer",
          company_name: "Acme",
          fit_score: null,
          dimension_coverage: null,
          dimensions: [
            {
              dimension: "skills_resume",
              available: false,
              score: null,
              weight: 0.35,
            },
          ],
          explanations: [],
        },
      ],
    });

    render(<ResumeRoute />);

    const badge = await screen.findByText("Not assessed yet");
    expect(screen.getByText("data coverage not recorded")).toBeTruthy();
    expect(screen.getByText("no data")).toBeTruthy();

    const card = badge.closest("li");
    expect(card?.textContent).toBeTruthy();
    expect(card?.textContent).not.toContain("0.00");
    expect(card?.textContent).not.toMatch(/(^|[^\d])0%/);
  });
});
