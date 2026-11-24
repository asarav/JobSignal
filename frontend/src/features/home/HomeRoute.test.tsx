import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HomeRoute } from "./HomeRoute";

const mocks = vi.hoisted(() => ({
  rankedJobs: vi.fn(),
  listApplications: vi.fn(),
  listCrawlRuns: vi.fn(),
  listResumeVersions: vi.fn(),
  listSources: vi.fn(),
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
  window.localStorage.clear();
  mocks.listApplications.mockResolvedValue({ applications: [], total: 0 });
  mocks.listCrawlRuns.mockResolvedValue({ runs: [] });
  mocks.listResumeVersions.mockResolvedValue({ versions: [] });
  mocks.listSources.mockResolvedValue({ sources: [] });
});

describe("HomeRoute headline counts", () => {
  it("uses the worker's whole-corpus counts, not the eight-row ranked page", async () => {
    mocks.rankedJobs.mockResolvedValue({
      jobs: [
        {
          job_id: "job_1",
          title: "Data Engineer",
          company_name: "Acme",
          location_text: "Berlin",
          fit_score: null,
          rank_score: null,
          risk_class: null,
        },
      ],
      total: 120,
      profile_id: "default",
      policy: "deprioritize",
      window: 500,
      window_truncated: false,
      hidden_by_policy: 7,
      needs_attention: 37,
      new_since: 64,
    });

    render(
      <MemoryRouter>
        <HomeRoute />
      </MemoryRouter>,
    );

    expect(await screen.findByText("37")).toBeTruthy();
    expect(screen.getByText("64")).toBeTruthy();
    expect(
      screen.getByText(/across the 500 most recent postings/),
    ).toBeTruthy();

    // One request is enough: the numbers are not recomputed from a page slice.
    expect(mocks.rankedJobs).toHaveBeenCalledTimes(1);
    expect(mocks.rankedJobs.mock.calls[0][0]).toMatchObject({
      limit: 8,
      seen_since: undefined,
    });

    const top = screen.getByText("Top ranked").closest("section");
    expect(top?.textContent).toContain("Fit —");
    expect(top?.textContent).not.toContain("0.00");
  });
});
