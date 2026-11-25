import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApplicationsRoute } from "./ApplicationsRoute";

const mocks = vi.hoisted(() => ({
  listApplications: vi.fn(),
  getSuppressions: vi.fn(),
  getSuppressionPolicy: vi.fn(),
  updateSuppressionPolicy: vi.fn(),
  restoreSuppressedJob: vi.fn(),
  getAnalytics: vi.fn(),
  updateApplicationStatus: vi.fn(),
  recordApplicationOutcome: vi.fn(),
  rankedJobs: vi.fn(),
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

const summary = {
  total: 6,
  applied: 3,
  active: 2,
  interviewing: 1,
  offers: 1,
  closed: 1,
  follow_up: 2,
  applied_last_7_days: 1,
  responded: 1,
  responded_last_7_days: 1,
};

function application(overrides: Record<string, unknown> = {}) {
  return {
    application_id: "app_1",
    company_id: "co_acme",
    company_name: "Acme",
    job_id: "job_1",
    job_title: "Data Engineer",
    status: "applied",
    applied_at: "2026-11-01T08:00:00Z",
    updated_at: "2026-11-10T08:00:00Z",
    created_at: "2026-11-01T08:00:00Z",
    notes: "",
    events: [
      {
        kind: "note",
        note: "Recruiter called",
        occurred_at: "2026-11-10T08:00:00Z",
      },
    ],
    outcomes: [],
    ...overrides,
  };
}

function renderApplications(path = "/applications") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <ApplicationsRoute />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.listApplications.mockResolvedValue({
    applications: [application()],
    total: 1,
    summary,
  });
  mocks.getSuppressions.mockResolvedValue({ suppressions: [], total: 0 });
  mocks.getSuppressionPolicy.mockResolvedValue({
    policy: {
      exact_repost_suppress: true,
      similar_role_cooldown_days: 180,
      similar_role_threshold: 0.75,
      company_wide_cooldown_enabled: false,
      company_wide_cooldown_days: 90,
    },
  });
  mocks.updateSuppressionPolicy.mockResolvedValue({ policy: {} });
  mocks.getAnalytics.mockResolvedValue({
    min_sample: 5,
    groups: [{ group: "workplace_type", key: "remote", n: 8, rate: 0.5 }],
  });
  mocks.updateApplicationStatus.mockResolvedValue({});
  mocks.recordApplicationOutcome.mockResolvedValue({ ok: true });
  mocks.rankedJobs.mockResolvedValue({ jobs: [], total: 0, new_since: 0 });
  mocks.listSavedJobs.mockResolvedValue({ jobs: [], total: 0 });
  mocks.listResumeVersions.mockResolvedValue({ versions: [] });
  mocks.listSources.mockResolvedValue({ sources: [] });
  mocks.listCrawlRuns.mockResolvedValue({ runs: [] });
});

describe("Applications workspace", () => {
  it("shows summary metrics and a waiting tab with the real count", async () => {
    renderApplications();

    expect(await screen.findByText("Application history")).toBeTruthy();
    expect(screen.getByText("Response rate")).toBeTruthy();
    expect(screen.getByText("33%")).toBeTruthy();
    expect(screen.getByRole("tab", { name: /Waiting 14\+ days\s*2/ })).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: /Waiting 14\+ days/ }));
    await waitFor(() =>
      expect(mocks.listApplications).toHaveBeenLastCalledWith(
        expect.objectContaining({ view: "follow_up", offset: 0, limit: 25 }),
      ),
    );
  });

  it("searches, expands the timeline and opens Insights", async () => {
    renderApplications();
    await screen.findByText("Data Engineer");

    fireEvent.change(screen.getByLabelText("Search applications"), {
      target: { value: "Acme" },
    });
    await waitFor(() =>
      expect(mocks.listApplications).toHaveBeenLastCalledWith(
        expect.objectContaining({ query: "Acme" }),
      ),
    );

    fireEvent.click(screen.getByText("Timeline and status"));
    expect(screen.getByText("Recruiter called")).toBeTruthy();
    expect(screen.getByLabelText("Status for Data Engineer")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Insights" }));
    expect(await screen.findByText("Response analytics")).toBeTruthy();
    expect(await screen.findByText("remote")).toBeTruthy();
    expect(mocks.getAnalytics).toHaveBeenCalled();
  });

  it("reads view, search and sort from a direct URL", async () => {
    renderApplications("/applications?view=offers&q=Acme&sort=updated");

    await waitFor(() =>
      expect(mocks.listApplications).toHaveBeenCalledWith(
        expect.objectContaining({
          view: "offers",
          query: "Acme",
          sort: "updated",
          limit: 25,
          offset: 0,
        }),
      ),
    );
  });

  it("pages application history without replacing the summary", async () => {
    const rows = Array.from({ length: 30 }, (_, index) =>
      application({
        application_id: `app_${index + 1}`,
        job_title: `Data Engineer ${index + 1}`,
      }),
    );
    mocks.listApplications.mockImplementation(
      (params: { limit?: number; offset?: number }) => {
        const limit = params.limit ?? 25;
        if (limit === 1) {
          return Promise.resolve({ applications: [], total: 30, summary });
        }
        const offset = params.offset ?? 0;
        return Promise.resolve({
          applications: rows.slice(offset, offset + limit),
          total: rows.length,
          summary,
        });
      },
    );
    renderApplications();

    expect(await screen.findByText("Data Engineer 1")).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Load 5 more" }));

    await waitFor(() =>
      expect(mocks.listApplications).toHaveBeenCalledWith(
        expect.objectContaining({ limit: 25, offset: 25 }),
      ),
    );
    expect(await screen.findByText("Showing 30 of 30 applications")).toBeTruthy();
  });

  it("moves hidden roles and suppression rules out of the history list", async () => {
    mocks.getSuppressions.mockResolvedValue({
      suppressions: [
        {
          job_id: "job_2",
          application_id: "app_1",
          job_title: "Platform Engineer",
          state: "active",
          reason: "similar_role_cooldown",
          explanation: "Similar to an applied role",
          expires_at: "2026-12-01T00:00:00Z",
        },
      ],
      total: 1,
    });
    renderApplications();
    await screen.findByText("Data Engineer");

    fireEvent.click(screen.getByRole("tab", { name: /Hidden/ }));
    expect(await screen.findByText("Platform Engineer")).toBeTruthy();
    expect(screen.getByText("Suppression rules")).toBeTruthy();
    expect(screen.queryByText("Application history")).toBeNull();
  });
});
