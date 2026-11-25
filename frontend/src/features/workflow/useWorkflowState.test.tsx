import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useWorkflowState } from "./useWorkflowState";

const mocks = vi.hoisted(() => ({
  rankedJobs: vi.fn(),
  listSavedJobs: vi.fn(),
  listApplications: vi.fn(),
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

beforeEach(() => {
  vi.clearAllMocks();
  mocks.rankedJobs.mockResolvedValue({ jobs: [{ job_id: "j1" }], total: 12 });
  mocks.listSavedJobs.mockResolvedValue({ jobs: [], total: 2 });
  mocks.listApplications.mockResolvedValue({
    applications: [{ application_id: "a1", job_id: "j1", status: "applied" }],
    total: 1,
  });
  mocks.listResumeVersions.mockResolvedValue({ versions: [{ id: "v1" }] });
  mocks.listSources.mockResolvedValue({ sources: [{ source_id: "s1" }] });
  mocks.listCrawlRuns.mockResolvedValue({
    runs: [
      {
        run_id: "r1",
        source_id: "s1",
        status: "completed",
        started_at: "2026-11-24T08:00:00Z",
        finished_at: "2026-11-24T08:05:00Z",
      },
    ],
  });
});

describe("useWorkflowState", () => {
  it("derives every stage input from whole-corpus worker data", async () => {
    const { result } = renderHook(() => useWorkflowState());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current).toMatchObject({
      hasResume: true,
      hasSources: true,
      hasMatches: true,
      matchCount: 12,
      savedCount: 2,
      appliedCount: 1,
      lastCrawl: "2026-11-24T08:05:00Z",
      error: null,
    });
    expect(mocks.rankedJobs).toHaveBeenCalledWith(
      expect.objectContaining({ limit: 1, seen_since: expect.any(String) }),
    );
  });

  it("reports a failed read instead of inventing an empty setup", async () => {
    mocks.listSources.mockRejectedValue(new Error("worker offline"));
    const { result } = renderHook(() => useWorkflowState());

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.error).toContain("worker offline");
  });
});
