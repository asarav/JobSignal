import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Drives the real `WorkerClient` and `workerStore` against a stub bridge that
 * answers with worker-shaped envelopes. Only the transport is replaced: the
 * envelope unwrapping, method names, store transitions and rendering are all
 * production code, so this covers the hello/health path end to end in the UI.
 */
const harness = vi.hoisted(() => {
  const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
  const responses: Record<string, unknown> = {
    "system.hello": {
      protocol_version: 1,
      app_version: "0.0.0",
      schema_version: 1,
      plugin_api_version: "1",
      capabilities: ["system.hello", "system.health"],
    },
    "system.health": {
      status: "healthy",
      app_version: "0.0.0",
      schema_version: 1,
      protocol_version: 1,
      data_dir: "C:/data/worker",
      disk_usage_bytes: 2048,
      plugin_health: [],
      queue_depth: 0,
    },
    "tasks.list": { tasks: [] },
  };
  const failures: Record<string, string> = {};
  return { calls, responses, failures };
});

vi.mock("../services/workerBridge", () => {
  const bridge = {
    isPreview: false,
    async request(method: string, params: Record<string, unknown>) {
      harness.calls.push({ method, params });
      if (method in harness.failures) {
        return {
          v: 1,
          type: "response",
          id: "stub",
          params: {},
          ok: false,
          error: {
            code: harness.failures[method],
            message: `stub failure for ${method}`,
            retryable: false,
            details: {},
          },
        };
      }
      if (!(method in harness.responses)) {
        return {
          v: 1,
          type: "response",
          id: "stub",
          params: {},
          ok: false,
          error: {
            code: "not_found",
            message: `stub bridge does not implement ${method}`,
            retryable: false,
            details: {},
          },
        };
      }
      return {
        v: 1,
        type: "response",
        id: "stub",
        params: {},
        ok: true,
        result: harness.responses[method],
      };
    },
    async onEvent() {
      return () => undefined;
    },
  };
  return {
    defaultBridge: bridge,
    PreviewBridge: bridge,
    TauriBridge: bridge,
    isTauriRuntime: () => false,
  };
});

const { useWorkerStore } = await import("../stores/workerStore");
const { HomeRoute } = await import("./home/HomeRoute");

beforeEach(() => {
  harness.calls.length = 0;
  useWorkerStore.setState({
    status: "idle",
    hello: null,
    health: null,
    error: null,
    isPreview: false,
  });
});

describe("hello/health path", () => {
  it("performs the handshake then health and stores both", async () => {
    await useWorkerStore.getState().refresh();

    expect(harness.calls.map((call) => call.method)).toEqual(["system.hello", "system.health"]);
    expect(harness.calls[0].params).toEqual({ protocol_version: 1, ui_version: "0.0.0" });

    const state = useWorkerStore.getState();
    expect(state.status).toBe("ready");
    expect(state.error).toBeNull();
    expect(state.hello?.protocol_version).toBe(1);
    expect(state.health?.status).toBe("healthy");
  });

  it("keeps worker internals off Today", async () => {
    await useWorkerStore.getState().refresh();

    render(
      <MemoryRouter initialEntries={["/"]}>
        <HomeRoute />
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /today/i })).toBeInTheDocument(),
    );
    expect(screen.queryByText("healthy")).not.toBeInTheDocument();
    expect(screen.queryByText("Worker health")).not.toBeInTheDocument();
  });

  it("records a failed handshake instead of leaving the UI empty", async () => {
    harness.failures["system.health"] = "internal.unexpected";
    try {
      await useWorkerStore.getState().refresh();
    } finally {
      delete harness.failures["system.health"];
    }

    const state = useWorkerStore.getState();
    expect(state.status).toBe("error");
    expect(state.error).toContain("internal.unexpected");
  });
});
