import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CollectJobsAction } from "./CollectJobsAction";

const mocks = vi.hoisted(() => ({
  addSource: vi.fn(),
  runDiscovery: vi.fn(),
  autoDiscover: vi.fn(),
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
  mocks.addSource.mockResolvedValue({ source_id: "s1", url: "https://example.test/jobs" });
  mocks.runDiscovery.mockResolvedValue({ ok: true });
  mocks.autoDiscover.mockResolvedValue({
    discovered: 4,
    registered: 2,
    crawled: 3,
    failed: 0,
    postings_harvested: 40,
  });
});

describe("CollectJobsAction", () => {
  it("runs one pass over every board and reports its counts", async () => {
    const onComplete = vi.fn();
    render(<CollectJobsAction onComplete={onComplete} />);

    fireEvent.click(screen.getByRole("button", { name: "Find new jobs everywhere" }));

    await waitFor(() => expect(mocks.autoDiscover).toHaveBeenCalled());
    expect(
      await screen.findByText(/2 new boards, 3 boards crawled, 40 postings found/),
    ).toBeInTheDocument();
    expect(onComplete).toHaveBeenCalled();
  });

  it("adds one known board and crawls just that board", async () => {
    render(<CollectJobsAction />);

    fireEvent.change(screen.getByLabelText("Career page or job board URL"), {
      target: { value: "https://example.test/jobs" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add one board and crawl it" }));

    await waitFor(() =>
      expect(mocks.addSource).toHaveBeenCalledWith({
        url: "https://example.test/jobs",
        interval_s: 3600,
      }),
    );
    expect(mocks.runDiscovery).toHaveBeenCalledWith({ source_id: "s1" });
    expect(await screen.findByText(/Board added and crawled/)).toBeInTheDocument();
  });
});
