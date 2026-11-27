import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ResumeRoute } from "./ResumeRoute";

const mocks = vi.hoisted(() => ({
  listResumeVersions: vi.fn(),
  listSources: vi.fn(),
  getMatchingResults: vi.fn(),
  getResumeProfile: vi.fn(),
  listSavedJobs: vi.fn(),
  setJobUserState: vi.fn(),
  runMatching: vi.fn(),
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
  mocks.listSources.mockResolvedValue({ sources: [] });
  mocks.listSavedJobs.mockResolvedValue({ jobs: [], total: 0 });
  mocks.setJobUserState.mockResolvedValue({});
  mocks.runMatching.mockResolvedValue({
    results: [],
    available_jobs: 0,
    considered: 0,
    scored: 0,
    reused: 0,
    truncated: false,
  });
  mocks.getResumeProfile.mockResolvedValue({
    profile: { id: "default", active_version_id: null, overrides: {} },
  });
});

describe("ResumeRoute matching run feedback", () => {
  it("sends the profile only and renders the run summary", async () => {
    mocks.runMatching.mockResolvedValue({
      results: [],
      available_jobs: 5,
      considered: 5,
      scored: 4,
      reused: 1,
      truncated: false,
    });
    mocks.getMatchingResults.mockResolvedValue({ weights: {}, results: [] });

    render(
      <MemoryRouter>
        <ResumeRoute />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Matches" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Recompute matches" }),
    );

    await waitFor(() =>
      expect(mocks.runMatching).toHaveBeenCalledWith({ profile_id: "default" }),
    );
    expect(await screen.findByText(/Scored 4 of 5 jobs/)).toBeTruthy();
    expect(screen.getByText(/unchanged and reused/)).toBeTruthy();
  });

  it("explains an empty corpus instead of showing a zero-job success", async () => {
    mocks.getMatchingResults.mockResolvedValue({ weights: {}, results: [] });

    render(
      <MemoryRouter>
        <ResumeRoute />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("tab", { name: "Matches" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Recompute matches" }),
    );

    expect(
      await screen.findByText(/Nothing has been collected yet/),
    ).toBeTruthy();
    expect(screen.getByText(/Find new jobs everywhere/)).toBeTruthy();
    expect(screen.queryByText(/Scored 0 of 0 jobs/)).toBeNull();
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
          location_text: "Remote (US)",
          workplace_type: "remote",
          missing_required_skills: ["rust"],
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

    render(
      <MemoryRouter>
        <ResumeRoute />
      </MemoryRouter>,
    );

    expect(screen.getByRole("tab", { name: "Profile details" })).toBeTruthy();
    const badge = await screen.findByText("Not assessed yet");
    expect(screen.getByText("data coverage not recorded")).toBeTruthy();
    expect(screen.getByText("no data")).toBeTruthy();

    const card = badge.closest("li");
    expect(card?.textContent).toBeTruthy();
    expect(card?.textContent).not.toContain("0.00");
    expect(card?.textContent).not.toMatch(/(^|[^\d])0%/);

    const view = screen.getAllByRole("link", { name: "View job" })[0];
    expect(view.getAttribute("href")).toBe("/jobs/job_1");
    expect(screen.getByText("Remote (US)")).toBeTruthy();
    expect(screen.getByText("Missing: rust")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Save for later" }));
    await waitFor(() =>
      expect(mocks.setJobUserState).toHaveBeenCalledWith({
        job_id: "job_1",
        state: "saved",
      }),
    );
  });
});
