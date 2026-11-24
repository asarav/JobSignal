import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { SearchRoute } from "./SearchRoute";

const mocks = vi.hoisted(() => ({
  listSearchProfiles: vi.fn(),
  listJobs: vi.fn(),
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
  mocks.listSearchProfiles.mockResolvedValue({ profiles: [] });
  mocks.listJobs.mockResolvedValue({
    jobs: [
      {
        job_id: "job_1",
        title: "Data Engineer",
        company_name: "Acme",
        location_text: "Berlin, Germany",
        posting_status: "active",
        canonical_url: "https://acme.test/jobs/1",
        provenance: ["extract.jsonld"],
      },
    ],
    total: 1,
  });
});

describe("SearchRoute results", () => {
  it("makes each hit an openable job with company, location and state", async () => {
    render(
      <MemoryRouter>
        <SearchRoute />
      </MemoryRouter>,
    );

    const link = await screen.findByRole("link", { name: "Data Engineer" });
    expect(link.getAttribute("href")).toBe("/jobs/job_1");
    expect(screen.getByText("Acme · Berlin, Germany")).toBeTruthy();
    expect(screen.getByText("active")).toBeTruthy();
  });

  it("says when company and location are not recorded instead of inventing them", async () => {
    mocks.listJobs.mockResolvedValue({
      jobs: [
        {
          job_id: "job_2",
          title: "Platform Engineer",
          posting_status: "active",
          canonical_url: "https://beta.test/jobs/2",
          provenance: [],
        },
      ],
      total: 1,
    });

    render(
      <MemoryRouter>
        <SearchRoute />
      </MemoryRouter>,
    );

    expect(
      await screen.findByText("Company and location not recorded"),
    ).toBeTruthy();
    expect(screen.getByRole("link", { name: "Platform Engineer" }).getAttribute("href")).toBe(
      "/jobs/job_2",
    );
  });
});
