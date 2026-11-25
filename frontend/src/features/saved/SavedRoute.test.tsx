import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../components/Toast";
import { SavedRoute } from "./SavedRoute";

const mocks = vi.hoisted(() => ({
  listSavedJobs: vi.fn(),
  listApplications: vi.fn(),
  setJobUserState: vi.fn(),
  createApplication: vi.fn(),
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
  mocks.listSavedJobs.mockResolvedValue({
    jobs: [{ job_id: "job_1", title: "Data Engineer", company_name: "Acme" }],
    total: 1,
  });
  mocks.listApplications.mockResolvedValue({ applications: [], total: 0 });
  mocks.createApplication.mockResolvedValue({ application_id: "a1" });
  mocks.setJobUserState.mockResolvedValue({});
});

describe("Saved", () => {
  it("marks a saved role applied with the same record as job detail", async () => {
    render(
      <MemoryRouter>
        <ToastProvider>
          <SavedRoute />
        </ToastProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Apply queue (1)")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Mark as applied" }));

    await waitFor(() =>
      expect(mocks.createApplication).toHaveBeenCalledWith({
        job_id: "job_1",
        status: "applied",
      }),
    );
    expect(await screen.findByText("Apply queue is clear")).toBeTruthy();
    expect(
      await screen.findByText(/Marked “Data Engineer” as applied/),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Open Applications" })).toHaveAttribute(
      "href",
      "/applications",
    );
  });

  it("advances to the next ready role after marking one applied", async () => {
    mocks.listSavedJobs.mockResolvedValue({
      jobs: [
        { job_id: "job_1", title: "Data Engineer", company_name: "Acme" },
        { job_id: "job_2", title: "Platform Engineer", company_name: "Acme" },
      ],
      total: 2,
    });
    render(
      <MemoryRouter>
        <ToastProvider>
          <SavedRoute />
        </ToastProvider>
      </MemoryRouter>,
    );

    expect(await screen.findByText("Data Engineer")).toBeTruthy();
    fireEvent.click(screen.getAllByRole("button", { name: "Mark as applied" })[0]);

    await waitFor(() => expect(screen.queryByText("Data Engineer")).toBeNull());
    expect(screen.getByText("Platform Engineer")).toBeTruthy();
  });

  it("reads the view, search and sort from the URL", async () => {
    render(
      <MemoryRouter initialEntries={["/saved?view=all&q=Acme&sort=company"]}>
        <SavedRoute />
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(mocks.listSavedJobs).toHaveBeenCalledWith(
        expect.objectContaining({ view: "all", query: "Acme", sort: "company" }),
      ),
    );
  });

  it("pages with a Load more action that reports the real page size", async () => {
    const rows = Array.from({ length: 30 }, (_, index) => ({
      job_id: `job_${index + 1}`,
      title: `Data Engineer ${index + 1}`,
      company_name: "Acme",
    }));
    mocks.listSavedJobs.mockImplementation(
      (params: { view?: string; limit?: number; offset?: number }) => {
        if (params.limit === 1) return Promise.resolve({ jobs: [], total: 30 });
        const offset = params.offset ?? 0;
        const limit = params.limit ?? 25;
        return Promise.resolve({ jobs: rows.slice(offset, offset + limit), total: 30 });
      },
    );
    render(
      <MemoryRouter>
        <SavedRoute />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Data Engineer 1")).toBeTruthy();
    fireEvent.click(await screen.findByRole("button", { name: "Load 5 more" }));

    await waitFor(() =>
      expect(mocks.listSavedJobs).toHaveBeenCalledWith(
        expect.objectContaining({ view: "ready", limit: 25, offset: 25 }),
      ),
    );
    expect(await screen.findByText("Showing 30 of 30 saved roles")).toBeTruthy();
  });

  it("sends an empty shortlist to Find jobs", async () => {
    mocks.listSavedJobs.mockResolvedValue({ jobs: [], total: 0 });
    render(
      <MemoryRouter>
        <SavedRoute />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Nothing shortlisted yet")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Find jobs" })).toHaveAttribute(
      "href",
      "/discover",
    );
  });
});
