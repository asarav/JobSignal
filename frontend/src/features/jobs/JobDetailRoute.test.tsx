import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { JobDetailRoute } from "./JobDetailRoute";

const mocks = vi.hoisted(() => ({
  getJobDetail: vi.fn(),
  listApplications: vi.fn(),
  listSavedJobs: vi.fn(),
  setJobUserState: vi.fn(),
  createApplication: vi.fn(),
  updateApplicationStatus: vi.fn(),
  getMatchingResults: vi.fn(),
  getPostingSignals: vi.fn(),
  getRiskHistory: vi.fn(),
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

function detail(overrides: Record<string, unknown> = {}) {
  return {
    job: {
      id: "job_1",
      job_id: "job_1",
      title: "Data Engineer",
      company_name: "Acme",
      location_text: "Berlin, Germany",
      workplace_type: "remote",
      salary_min: 90000,
      salary_max: 120000,
      salary_currency: "EUR",
      fit_score: 0.78,
      description_text: "Build reliable pipelines with Python and SQL.",
      canonical_url: "https://acme.test/jobs/1",
      posting_status: "active",
      first_seen_at: "2026-11-01T00:00:00Z",
      last_seen_at: "2026-11-20T00:00:00Z",
      ...overrides,
    },
    snapshots: [],
    observations: [{ observed_at: "2026-11-01T00:00:00Z" }],
    relations: [],
    reposts: [],
  };
}

function renderDetail() {
  return render(
    <MemoryRouter initialEntries={["/jobs/job_1"]}>
      <Routes>
        <Route path="/jobs/:id" element={<JobDetailRoute />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getJobDetail.mockResolvedValue(detail());
  mocks.listApplications.mockResolvedValue({ applications: [], total: 0 });
  mocks.listSavedJobs.mockResolvedValue({ jobs: [], total: 0 });
  mocks.setJobUserState.mockResolvedValue({
    job_id: "job_1",
    state: "saved",
    hidden: false,
  });
  mocks.createApplication.mockResolvedValue({ application_id: "a1" });
  mocks.updateApplicationStatus.mockResolvedValue({ application_id: "a1" });
  mocks.getMatchingResults.mockResolvedValue({ results: [], weights: {} });
  mocks.getPostingSignals.mockResolvedValue({
    job_id: "job_1",
    assessment: null,
    signals: [],
  });
  mocks.getRiskHistory.mockResolvedValue({ job_id: "job_1", changes: [] });
});

describe("JobDetailRoute posting content", () => {
  it("renders the description, company, location, salary and an original link", async () => {
    renderDetail();

    expect(
      await screen.findByText("Build reliable pipelines with Python and SQL."),
    ).toBeTruthy();
    expect(screen.getByText("Acme")).toBeTruthy();
    expect(screen.getByText("Berlin, Germany · remote")).toBeTruthy();
    expect(screen.getByText(/^Salary EUR 90,000/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Open original" })).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Why this job" }));
    expect(await screen.findByText(/Stored fit 0\.78/)).toBeTruthy();
  });

  it("says what is missing rather than showing zero or a blank", async () => {
    mocks.getJobDetail.mockResolvedValue(
      detail({
        company_name: null,
        location_text: null,
        workplace_type: "unknown",
        salary_min: null,
        salary_max: null,
        salary_currency: null,
        description_text: "",
        fit_score: null,
        canonical_url: null,
      }),
    );

    renderDetail();

    expect(await screen.findByText("Company not recorded")).toBeTruthy();
    expect(screen.getByText("Location not recorded")).toBeTruthy();
    expect(screen.getByText("Salary not listed")).toBeTruthy();
    expect(
      screen.getByText(/No description was stored for this posting/),
    ).toBeTruthy(    );
    expect(screen.getByText("No original link recorded")).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: "Why this job" }));
    expect(await screen.findByText(/No fit score yet for this job/)).toBeTruthy();
    expect(document.body.textContent).not.toContain("0.00");
  });

  it("saves through the same job_user_state list Saved Jobs reads", async () => {
    renderDetail();

    const save = await screen.findByRole("button", { name: "Save for later" });
    fireEvent.click(save);

    await waitFor(() =>
      expect(mocks.setJobUserState).toHaveBeenCalledWith({
        job_id: "job_1",
        state: "saved",
      }),
    );
    expect(await screen.findByRole("button", { name: "Saved ✓" })).toBeTruthy();
  });

  it("shows an already-saved job as saved", async () => {
    mocks.listSavedJobs.mockResolvedValue({
      jobs: [{ job_id: "job_1", title: "Data Engineer" }],
      total: 1,
    });

    renderDetail();

    expect(await screen.findByRole("button", { name: "Saved ✓" })).toBeTruthy();
    expect(mocks.setJobUserState).not.toHaveBeenCalled();
  });

  it("advances a saved job to applied and then to an updated status", async () => {
    mocks.listSavedJobs.mockResolvedValue({
      jobs: [{ job_id: "job_1", title: "Data Engineer" }],
      total: 1,
    });
    mocks.listApplications
      .mockResolvedValueOnce({ applications: [], total: 0 })
      .mockResolvedValue({
        applications: [
          { application_id: "a1", job_id: "job_1", status: "applied" },
        ],
        total: 1,
      });

    renderDetail();
    fireEvent.click(await screen.findByRole("button", { name: "Mark as applied" }));

    await waitFor(() =>
      expect(mocks.createApplication).toHaveBeenCalledWith({
        job_id: "job_1",
        status: "applied",
      }),
    );
    expect(await screen.findByLabelText("Update application status")).toBeTruthy();
    expect(screen.getByText(/Track it in Applications/)).toBeTruthy();

    fireEvent.change(screen.getByLabelText("Update application status"), {
      target: { value: "interviewing" },
    });
    await waitFor(() =>
      expect(mocks.updateApplicationStatus).toHaveBeenCalledWith({
        application_id: "a1",
        status: "interviewing",
      }),
    );
  });

  it("keeps Autofill and posting history behind explicit opens", async () => {
    renderDetail();

    await screen.findByRole("button", { name: "Save for later" });
    expect(screen.queryByText("Prepare application", { selector: "h2" })).toBeNull();
    expect(screen.queryByText("First seen")).toBeNull();
    expect(screen.queryByText("Repost history")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Posting facts/ }));
    expect(screen.getByText("First seen")).toBeTruthy();
    expect(screen.getByText("Times observed")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Prepare application" }));
    expect(
      await screen.findByRole("heading", { name: "Prepare application" }),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Posting history/ }));
    expect(screen.getByText("Repost history")).toBeTruthy();
    expect(screen.getByText(/No reposts detected/)).toBeTruthy();
  });
});
