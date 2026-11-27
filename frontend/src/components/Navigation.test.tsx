import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Navigation } from "./Navigation";

const mocks = vi.hoisted(() => ({
  listResumeVersions: vi.fn(),
  listSources: vi.fn(),
  listSavedJobs: vi.fn(),
  listApplications: vi.fn(),
}));

vi.mock("../services/workerClient", () => ({
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
  mocks.listApplications.mockResolvedValue({ applications: [], total: 0, summary: {} });
});

function renderNav(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Navigation />
    </MemoryRouter>,
  );
}

describe("Navigation", () => {
  it("groups destinations in workflow order and marks the current page", async () => {
    renderNav("/");

    expect(screen.getByText("Search")).toBeTruthy();
    expect(screen.getByText("You")).toBeTruthy();
    expect(screen.getByText("Insights")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Today" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(screen.getByRole("link", { name: "Find jobs" })).toHaveAttribute(
      "href",
      "/discover",
    );
    expect(screen.queryByRole("link", { name: "Search Profiles" })).toBeNull();
    expect(
      await screen.findByText("Setup needed: import a resume, add a job source"),
    ).toBeTruthy();
  });

  it("shows ready and follow-up counts as badges", async () => {
    mocks.listSavedJobs.mockResolvedValue({ jobs: [], total: 3 });
    mocks.listApplications.mockResolvedValue({
      applications: [],
      total: 0,
      summary: { follow_up: 2 },
    });
    renderNav("/");

    expect(await screen.findByText("3")).toBeTruthy();
    expect(screen.getByText("2")).toBeTruthy();
  });

  it("reports a ready profile and collapses Insights", async () => {
    mocks.listResumeVersions.mockResolvedValue({ versions: [{ id: "v1" }] });
    mocks.listSources.mockResolvedValue({ sources: [{ source_id: "s1" }] });
    renderNav("/resume");

    expect(await screen.findByText("Ready")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Insights/ }));
    expect(screen.queryByRole("link", { name: "Companies" })).toBeNull();
  });
});
