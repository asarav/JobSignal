import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DiscoverRoute } from "./DiscoverRoute";

const mocks = vi.hoisted(() => ({
  rankedJobs: vi.fn(),
  listJobs: vi.fn(),
  listSearchProfiles: vi.fn(),
  setJobUserState: vi.fn(),
  saveSearchProfile: vi.fn(),
  deleteSearchProfile: vi.fn(),
  listSources: vi.fn(),
  addSource: vi.fn(),
  runDiscovery: vi.fn(),
  autoDiscover: vi.fn(),
  getDiscoveryStatus: vi.fn(),
  onEvent: vi.fn(),
  getJobDetail: vi.fn(),
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

const rankedRow = {
  job_id: "job_1",
  title: "Data Engineer",
  company_name: "Acme",
  location_text: "Berlin",
  salary_min: 90000,
  salary_max: 120000,
  salary_currency: "EUR",
  fit_score: 0.78,
  credibility: 0.9,
  rank_score: 0.7,
  rank_reasons: ["Strong skill overlap", "Posting looks active"],
  saved: false,
};

function renderFind(path = "/discover") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <DiscoverRoute />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  mocks.listSearchProfiles.mockResolvedValue({ profiles: [] });
  mocks.rankedJobs.mockResolvedValue({
    jobs: [rankedRow],
    total: 1,
    hidden_by_policy: 0,
  });
  mocks.listJobs.mockResolvedValue({ jobs: [rankedRow], total: 1 });
  mocks.setJobUserState.mockResolvedValue({});
  mocks.listSources.mockResolvedValue({ sources: [] });
  mocks.addSource.mockResolvedValue({ source_id: "source_1" });
  mocks.runDiscovery.mockResolvedValue({ ok: true });
  mocks.autoDiscover.mockResolvedValue({
    registered: 2,
    crawled: 3,
    postings_harvested: 40,
    failed: 0,
  });
  mocks.getDiscoveryStatus.mockResolvedValue({
    running: false,
    interval_minutes: 60,
    last_pass_at: "2026-11-21T07:30:00Z",
    next_pass_at: "2026-11-21T08:30:00Z",
    last_summary: {},
    sources: 4,
    enabled_sources: 4,
    last_crawl_at: null,
    note: "",
  });
  mocks.onEvent.mockResolvedValue(() => undefined);
  mocks.getJobDetail.mockResolvedValue({
    job: { description_text: "Build Python and SQL pipelines." },
  });
});

describe("Find jobs", () => {
  it("shows ranked rows with salary and a quiet explanation", async () => {
    renderFind();

    expect(await screen.findByText("Data Engineer")).toBeTruthy();
    expect(screen.getByText(/EUR 90,000/)).toBeTruthy();
    expect(screen.queryByText("Strong skill overlap")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: /Why this rank/ }));
    expect(screen.getByText("Strong skill overlap")).toBeTruthy();
    expect(screen.getByText("0.70")).toBeTruthy();
  });

  it("switches to the unranked Search all tab", async () => {
    renderFind();
    await screen.findByText("Data Engineer");

    fireEvent.click(screen.getByRole("tab", { name: "Search all" }));

    await waitFor(() => expect(mocks.listJobs).toHaveBeenCalled());
    expect(await screen.findByText("All matching jobs")).toBeTruthy();
    expect(mocks.listJobs.mock.calls[0][0]).toMatchObject({
      query: undefined,
      filters: {},
      limit: 25,
    });
  });

  it("saves through the same job_user_state list Saved reads", async () => {
    renderFind();
    fireEvent.click(await screen.findByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(mocks.setJobUserState).toHaveBeenCalledWith({
        job_id: "job_1",
        state: "saved",
      }),
    );
  });

  it("offers a full discovery pass when nothing has been collected", async () => {
    mocks.rankedJobs.mockResolvedValue({ jobs: [], total: 0, hidden_by_policy: 0 });
    renderFind();

    // The empty-state action is the one next to the add-a-board input; the
    // discovery strip above it has the same label by design.
    const container = (await screen.findByLabelText("Career page or job board URL"))
      .closest("div.w-full");
    expect(container).not.toBeNull();
    fireEvent.click(
      within(container as HTMLElement).getByRole("button", {
        name: "Find new jobs everywhere",
      }),
    );

    await waitFor(() => expect(mocks.autoDiscover).toHaveBeenCalled());
    expect(
      await screen.findByText(/2 new boards, 3 boards crawled, 40 postings found/),
    ).toBeTruthy();
  });

  it("can still add one named board and crawl it", async () => {
    mocks.rankedJobs.mockResolvedValue({ jobs: [], total: 0, hidden_by_policy: 0 });
    renderFind();

    fireEvent.change(await screen.findByLabelText("Career page or job board URL"), {
      target: { value: "https://example.test/jobs" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Add one board and crawl it" }),
    );

    await waitFor(() =>
      expect(mocks.addSource).toHaveBeenCalledWith({
        url: "https://example.test/jobs",
        interval_s: 3600,
      }),
    );
    expect(mocks.runDiscovery).toHaveBeenCalledWith({ source_id: "source_1" });
  });

  it("filters to the profile area by default and can widen it", async () => {
    renderFind();
    await screen.findByText("Data Engineer");
    expect(mocks.rankedJobs).toHaveBeenLastCalledWith(
      expect.objectContaining({ include_out_of_area: false }),
    );

    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    fireEvent.click(screen.getByLabelText("Only my area (from my preferences)"));

    await waitFor(() =>
      expect(mocks.rankedJobs).toHaveBeenLastCalledWith(
        expect.objectContaining({ include_out_of_area: true }),
      ),
    );
  });

  it("pages ranked results and resets the offset when a preset changes", async () => {
    const allRows = Array.from({ length: 40 }, (_, index) => ({
      ...rankedRow,
      job_id: `job_${index + 1}`,
      title: `Data Engineer ${index + 1}`,
    }));
    mocks.rankedJobs.mockImplementation(
      (params: { offset?: number; limit?: number }) => {
        const offset = params.offset ?? 0;
        const limit = params.limit ?? 25;
        return Promise.resolve({
          jobs: allRows.slice(offset, offset + limit),
          total: allRows.length,
          hidden_by_policy: 0,
        });
      },
    );
    renderFind();

    fireEvent.click(await screen.findByRole("button", { name: /Load 15 more/ }));
    await waitFor(() =>
      expect(mocks.rankedJobs).toHaveBeenLastCalledWith(
        expect.objectContaining({ offset: 25, limit: 25 }),
      ),
    );

    fireEvent.click(screen.getByRole("button", { name: "Strong fit" }));
    await waitFor(() =>
      expect(mocks.rankedJobs).toHaveBeenLastCalledWith(
        expect.objectContaining({ min_fit: 0.75, offset: 0, sort: "fit" }),
      ),
    );
  });

  it("honours the direct new-match link with the stored cutoff", async () => {
    window.localStorage.setItem(
      "jobsignal.lastVisit",
      String(Date.parse("2026-11-20T08:00:00Z")),
    );
    renderFind("/discover?new=1");

    await waitFor(() =>
      expect(mocks.rankedJobs).toHaveBeenLastCalledWith(
        expect.objectContaining({
          filters: expect.objectContaining({
            first_seen_after: "2026-11-20T08:00:00Z",
          }),
        }),
      ),
    );
  });

  it("uses the selected page size in the Load more label and request", async () => {
    const rows = Array.from({ length: 60 }, (_, index) => ({
      ...rankedRow,
      job_id: `job_${index + 1}`,
      title: `Data Engineer ${index + 1}`,
    }));
    mocks.rankedJobs.mockImplementation(
      (params: { offset?: number; limit?: number }) => {
        const offset = params.offset ?? 0;
        const limit = params.limit ?? 25;
        return Promise.resolve({
          jobs: rows.slice(offset, offset + limit),
          total: rows.length,
          hidden_by_policy: 0,
        });
      },
    );
    renderFind();
    await screen.findByText("Data Engineer 1");

    fireEvent.change(screen.getByLabelText("Results per page"), { target: { value: "50" } });
    await waitFor(() =>
      expect(mocks.rankedJobs).toHaveBeenLastCalledWith(
        expect.objectContaining({ limit: 50, offset: 0 }),
      ),
    );
    expect(await screen.findByRole("button", { name: "Load 10 more" })).toBeTruthy();
  });

  it("ignores focus shortcuts while typing and supports back and next", async () => {
    mocks.rankedJobs.mockResolvedValue({
      jobs: [
        rankedRow,
        { ...rankedRow, job_id: "job_2", title: "Platform Engineer" },
      ],
      total: 2,
      hidden_by_policy: 0,
    });
    renderFind();
    fireEvent.click(await screen.findByRole("button", { name: "Focus review" }));
    expect(await screen.findByText(/1 of 2 loaded/)).toBeTruthy();

    const input = document.createElement("input");
    document.body.appendChild(input);
    fireEvent.keyDown(input, { key: "s" });
    expect(mocks.setJobUserState).not.toHaveBeenCalled();
    input.remove();

    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(await screen.findByText(/2 of 2 loaded/)).toBeTruthy();
    fireEvent.keyDown(window, { key: "k" });
    expect(await screen.findByText(/1 of 2 loaded/)).toBeTruthy();
  });

  it("reviews one job at a time with keyboard save and reports completion", async () => {
    renderFind();
    fireEvent.click(await screen.findByRole("button", { name: "Focus review" }));

    expect(await screen.findByText(/1 of 1 loaded/)).toBeTruthy();
    fireEvent.keyDown(window, { key: "s" });
    await waitFor(() =>
      expect(mocks.setJobUserState).toHaveBeenCalledWith({
        job_id: "job_1",
        state: "saved",
      }),
    );
    expect(await screen.findByText("Focus queue cleared")).toBeTruthy();
    expect(screen.getByText(/1 saved · 0 dismissed/)).toBeTruthy();
  });

  it("sends filters through the toolbar rather than managing sources here", async () => {
    renderFind();
    await screen.findByText("Data Engineer");
    fireEvent.click(screen.getByRole("button", { name: "Filters" }));
    fireEvent.change(screen.getByLabelText("Title contains"), {
      target: { value: "Engineer" },
    });

    await waitFor(() =>
      expect(mocks.rankedJobs).toHaveBeenLastCalledWith(
        expect.objectContaining({
          filters: { title_include: "Engineer" },
        }),
      ),
    );
    expect(screen.queryByRole("button", { name: /Add one board and crawl/ })).toBeNull();
  });
});
