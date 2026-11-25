import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { HomeRoute } from "./HomeRoute";

const mocks = vi.hoisted(() => ({
  rankedJobs: vi.fn(),
  listApplications: vi.fn(),
  listSavedJobs: vi.fn(),
  listResumeVersions: vi.fn(),
  listSources: vi.fn(),
  listCrawlRuns: vi.fn(),
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

function ranked(overrides: Record<string, unknown> = {}) {
  return {
    jobs: [
      {
        job_id: "job_1",
        title: "Data Engineer",
        company_name: "Acme",
        location_text: "Berlin",
        fit_score: null,
        risk_class: null,
      },
      { job_id: "job_2", title: "Backend Engineer", fit_score: 0.78 },
      { job_id: "job_3", title: "Platform Engineer", fit_score: 0.6 },
      { job_id: "job_4", title: "Fourth role", fit_score: 0.5 },
    ],
    total: 120,
    window: 500,
    window_truncated: false,
    hidden_by_policy: 7,
    needs_attention: 37,
    new_since: 64,
    ...overrides,
  };
}

function summary(overrides: Record<string, unknown> = {}) {
  return {
    total: 0,
    applied: 0,
    active: 0,
    interviewing: 0,
    offers: 0,
    closed: 0,
    follow_up: 0,
    applied_last_7_days: 0,
    responded: 0,
    responded_last_7_days: 0,
    ...overrides,
  };
}

function renderHome() {
  return render(
    <MemoryRouter>
      <HomeRoute />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  mocks.listApplications.mockResolvedValue({ applications: [], total: 0, summary: summary() });
  mocks.listSavedJobs.mockResolvedValue({ jobs: [], total: 0 });
  mocks.listResumeVersions.mockResolvedValue({ versions: [] });
  mocks.listSources.mockResolvedValue({ sources: [] });
  mocks.listCrawlRuns.mockResolvedValue({ runs: [] });
});

describe("HomeRoute", () => {
  it("uses whole-corpus counts and only the top three focus rows", async () => {
    mocks.listResumeVersions.mockResolvedValue({ versions: [{ id: "v1" }] });
    mocks.listSources.mockResolvedValue({ sources: [{ source_id: "s1" }] });
    mocks.listApplications.mockResolvedValue({
      applications: [{ application_id: "a1", job_id: "job_1", status: "applied" }],
      total: 1,
      summary: summary({ total: 1, applied: 1 }),
    });
    mocks.rankedJobs.mockResolvedValue(ranked());
    window.localStorage.setItem("jobsignal.lastVisit", String(Date.now() - 60_000));
    renderHome();

    expect(
      await screen.findByRole("link", { name: /37 postings need verification/ }),
    ).toBeTruthy();
    expect(screen.getByText(/64 new since your last visit/)).toBeTruthy();
    expect(screen.getByText(/across the 500 most recent postings/)).toBeTruthy();
    expect(screen.getByText("Data Engineer")).toBeTruthy();
    expect(screen.queryByText("Fourth role")).not.toBeInTheDocument();

    expect(mocks.rankedJobs).toHaveBeenCalledTimes(1);
    expect(mocks.rankedJobs.mock.calls[0][0]).toMatchObject({
      limit: 3,
      seen_since: expect.any(String),
    });
    expect(screen.queryByText("Crawl status")).not.toBeInTheDocument();
    expect(screen.queryByText(/Worker health/)).not.toBeInTheDocument();
  });

  it("makes importing a resume the current step when setup is empty", async () => {
    mocks.rankedJobs.mockResolvedValue(
      ranked({ jobs: [], total: 0, needs_attention: 0, new_since: null }),
    );
    renderHome();

    const plan = await screen.findByRole("region", { name: /your search plan/i });
    expect(plan.textContent).toContain("Add your resume");
    expect(screen.getAllByRole("link", { name: "Import resume" }).length).toBeGreaterThan(0);
  });

  it("switches to the daily focus once setup is complete, even before saving", async () => {
    mocks.listResumeVersions.mockResolvedValue({ versions: [{ id: "v1" }] });
    mocks.listSources.mockResolvedValue({ sources: [{ source_id: "s1" }] });
    mocks.rankedJobs.mockResolvedValue(ranked());
    renderHome();

    expect(await screen.findByText("Today’s focus")).toBeTruthy();
    expect(screen.getByText("Save a match to start your apply queue.")).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "Review new matches" })[0]).toHaveAttribute(
      "href",
      "/discover?new=1",
    );
    const plan = screen.getByRole("region", { name: /your search plan/i });
    expect(plan.textContent).toContain("Review new matches");
  });

  it("keeps the previous last-visit baseline while the user is reviewing", async () => {
    mocks.listResumeVersions.mockResolvedValue({ versions: [{ id: "v1" }] });
    mocks.listSources.mockResolvedValue({ sources: [{ source_id: "s1" }] });
    mocks.rankedJobs.mockResolvedValue(ranked());
    const previous = Date.parse("2026-11-20T08:00:00Z");
    window.localStorage.setItem("jobsignal.lastVisit", String(previous));
    renderHome();

    await screen.findByText("Today’s focus");
    expect(window.localStorage.getItem("jobsignal.lastVisit")).toBe(String(previous));
    expect(mocks.rankedJobs.mock.calls[0][0]).toMatchObject({
      seen_since: "2026-11-20T08:00:00Z",
    });
  });

  it("offers the three daily queues once the user is established", async () => {
    mocks.listResumeVersions.mockResolvedValue({ versions: [{ id: "v1" }] });
    mocks.listSources.mockResolvedValue({ sources: [{ source_id: "s1" }] });
    mocks.listSavedJobs.mockResolvedValue({ jobs: [{ job_id: "job_1" }], total: 1 });
    mocks.listApplications.mockResolvedValue({
      applications: [{ application_id: "a1", job_id: "job_1", status: "applied" }],
      total: 1,
      summary: summary({ total: 1, applied: 1, follow_up: 2, applied_last_7_days: 3 }),
    });
    mocks.rankedJobs.mockResolvedValue(ranked());
    renderHome();

    expect(await screen.findByText("Today’s focus")).toBeTruthy();
    expect(screen.getByText("This week")).toBeTruthy();
    expect(screen.getAllByRole("link", { name: "Open apply queue" })[0]).toHaveAttribute(
      "href",
      "/saved?view=ready",
    );
    expect(screen.getByRole("link", { name: "Review waiting" })).toHaveAttribute(
      "href",
      "/applications?view=follow_up",
    );
    expect(screen.getAllByRole("link", { name: "Review new matches" })[0]).toHaveAttribute(
      "href",
      "/discover?new=1",
    );
    const plan = screen.getByRole("region", { name: /your search plan/i });
    expect(plan.textContent).toContain("Open apply queue");
  });
});
