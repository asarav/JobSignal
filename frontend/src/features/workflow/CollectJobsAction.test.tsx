import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CollectJobsAction } from "./CollectJobsAction";

const mocks = vi.hoisted(() => ({
  listSources: vi.fn(),
  addSource: vi.fn(),
  runDiscovery: vi.fn(),
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
  mocks.listSources.mockResolvedValue({ sources: [] });
  mocks.addSource.mockResolvedValue({ source_id: "s1", url: "https://example.test/jobs" });
  mocks.runDiscovery.mockResolvedValue({ ok: true });
});

describe("CollectJobsAction", () => {
  it("adds a source and immediately crawls it, then reports completion", async () => {
    const onComplete = vi.fn();
    render(<CollectJobsAction onComplete={onComplete} />);

    fireEvent.change(await screen.findByLabelText("Career page or job board URL"), {
      target: { value: "https://example.test/jobs" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add source and crawl" }));

    await waitFor(() =>
      expect(mocks.addSource).toHaveBeenCalledWith({
        url: "https://example.test/jobs",
        interval_s: 3600,
      }),
    );
    expect(mocks.runDiscovery).toHaveBeenCalledWith({ source_id: "s1" });
    expect(await screen.findByText(/Source added and crawled/)).toBeInTheDocument();
    expect(onComplete).toHaveBeenCalled();
  });

  it("crawls an existing source instead of asking for another", async () => {
    mocks.listSources.mockResolvedValue({
      sources: [{ source_id: "s1", url: "https://boards.example/acme" }],
    });
    render(<CollectJobsAction />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Crawl https://boards.example/acme" }),
    );
    await waitFor(() =>
      expect(mocks.runDiscovery).toHaveBeenCalledWith({ source_id: "s1" }),
    );
    expect(mocks.addSource).not.toHaveBeenCalled();
  });
});
