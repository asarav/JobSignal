import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToastProvider } from "../../components/Toast";
import { DiscoveryStatus } from "./DiscoveryStatus";

const mocks = vi.hoisted(() => ({
  getDiscoveryStatus: vi.fn(),
  onEvent: vi.fn(),
  autoDiscover: vi.fn(),
}));

vi.mock("../../services/workerClient", () => ({ workerClient: mocks }));

type Handler = (event: { topic: string; data?: unknown }) => void;
let handler: Handler | null = null;

beforeEach(() => {
  vi.clearAllMocks();
  handler = null;
  mocks.getDiscoveryStatus.mockResolvedValue({
    running: false,
    interval_minutes: 60,
    last_pass_at: null,
    next_pass_at: null,
    enabled_sources: 2,
    sources: 2,
    last_summary: null,
  });
  mocks.onEvent.mockImplementation(async (callback: Handler) => {
    handler = callback;
    return () => undefined;
  });
  mocks.autoDiscover.mockResolvedValue({
    registered: 2,
    crawled: 3,
    postings_harvested: 142,
    failed: 0,
  });
});

function renderStatus(onJobsChanged = vi.fn()) {
  render(
    <ToastProvider>
      <DiscoveryStatus onJobsChanged={onJobsChanged} />
    </ToastProvider>,
  );
  return onJobsChanged;
}

describe("DiscoveryStatus", () => {
  it("refreshes jobs and reports a background pass that found something", async () => {
    const onJobsChanged = renderStatus();
    await waitFor(() => expect(handler).not.toBeNull());

    await act(async () => {
      handler?.({
        topic: "discovery.pass_completed",
        data: { registered: 2, crawled: 3, postings_harvested: 142, failed: 0 },
      });
    });

    expect(onJobsChanged).toHaveBeenCalled();
    expect(
      await screen.findByText(
        /Discovery pass finished: 2 new boards · 3 boards crawled · 142 postings found/,
      ),
    ).toBeTruthy();
  });

  it("refreshes the list when an individual crawl completes", async () => {
    const onJobsChanged = renderStatus();
    await waitFor(() => expect(handler).not.toBeNull());

    await act(async () => {
      handler?.({
        topic: "crawl.completed",
        data: { run_id: "r1", source_id: "s1", stats: { discovered: 4 } },
      });
    });

    expect(onJobsChanged).toHaveBeenCalled();
  });

  it("runs a manual pass from the button and toasts its counts", async () => {
    renderStatus();
    fireEvent.click(
      await screen.findByRole("button", { name: "Find new jobs everywhere" }),
    );

    await waitFor(() => expect(mocks.autoDiscover).toHaveBeenCalled());
    expect(
      await screen.findByText(/Discovery pass finished: 2 new boards/),
    ).toBeTruthy();
  });
});
