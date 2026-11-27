import { act, render, screen } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getDiscoveryStatus: vi.fn(),
  onEvent: vi.fn(),
}));

vi.mock("../services/workerClient", () => ({
  workerClient: {
    isPreview: false,
    getDiscoveryStatus: mocks.getDiscoveryStatus,
    onEvent: mocks.onEvent,
  },
}));

import { resetLiveRefreshForTests, useLiveRefresh } from "./useLiveRefresh";

function Probe() {
  const [refreshes, setRefreshes] = useState(0);
  useLiveRefresh(() => setRefreshes((value) => value + 1));
  return <span data-testid="refreshes">{refreshes}</span>;
}

describe("useLiveRefresh fallback", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mocks.getDiscoveryStatus.mockReset();
    mocks.onEvent.mockReset();
    mocks.onEvent.mockRejectedValue(new Error("event bridge unavailable"));
    resetLiveRefreshForTests();
  });

  it("polls while discovery is active and settles after it goes idle", async () => {
    mocks.getDiscoveryStatus.mockResolvedValue({
      running: true,
      active_crawls: 1,
      initial_pending: 1,
    });
    render(<Probe />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mocks.onEvent).toHaveBeenCalledTimes(1);
    expect(mocks.getDiscoveryStatus).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("refreshes")).toHaveTextContent("1");

    mocks.getDiscoveryStatus.mockResolvedValue({
      running: false,
      active_crawls: 0,
      initial_pending: 0,
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(mocks.getDiscoveryStatus).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("refreshes")).toHaveTextContent("2");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });
    expect(mocks.getDiscoveryStatus.mock.calls.length).toBeGreaterThan(2);
    expect(screen.getByTestId("refreshes")).toHaveTextContent("2");
  });

  it("catches a crawl that starts and ends after the page is already idle", async () => {
    mocks.getDiscoveryStatus.mockResolvedValue({
      running: false,
      postings_found: 10,
      last_crawl_at: "2026-11-27T10:00:00Z",
    });
    render(<Probe />);
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(screen.getByTestId("refreshes")).toHaveTextContent("0");

    mocks.getDiscoveryStatus.mockResolvedValue({
      running: false,
      postings_found: 11,
      last_crawl_at: "2026-11-27T10:00:10Z",
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000);
    });
    expect(screen.getByTestId("refreshes")).toHaveTextContent("1");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(screen.getByTestId("refreshes")).toHaveTextContent("1");
  });
});