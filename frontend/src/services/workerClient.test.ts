import { describe, expect, it } from "vitest";
import { WorkerClient, WorkerError } from "./workerClient";
import type { WorkerBridge } from "./workerBridge";
import type { Envelope } from "../types/generated";

class StubBridge implements WorkerBridge {
  readonly isPreview = false;
  readonly calls: Array<{ method: string; params: Record<string, unknown> }> = [];

  constructor(private readonly response: Envelope) {}

  async request(method: string, params: Record<string, unknown>): Promise<Envelope> {
    this.calls.push({ method, params });
    return this.response;
  }

  async onEvent() {
    return () => undefined;
  }
}

const ok = (result: unknown): Envelope => ({
  v: 1,
  type: "response",
  id: "1",
  params: {},
  ok: true,
  result: result as Record<string, unknown>,
});

describe("WorkerClient", () => {
  it("unwraps a successful response", async () => {
    const bridge = new StubBridge(
      ok({
        protocol_version: 1,
        app_version: "0.0.0",
        schema_version: 1,
        plugin_api_version: "1",
        capabilities: ["system.hello"],
      }),
    );
    const client = new WorkerClient(bridge);
    const hello = await client.hello({ protocol_version: 1, ui_version: "test" });
    expect(hello.protocol_version).toBe(1);
    expect(bridge.calls[0].method).toBe("system.hello");
  });

  it("maps worker errors to WorkerError", async () => {
    const bridge = new StubBridge({
      v: 1,
      type: "response",
      id: "2",
      params: {},
      ok: false,
      error: {
        code: "validation.invalid_params",
        message: "bad params",
        retryable: false,
        details: { field: "url" },
      },
    });
    const client = new WorkerClient(bridge);
    await expect(client.health()).rejects.toBeInstanceOf(WorkerError);
    await expect(client.health()).rejects.toMatchObject({
      code: "validation.invalid_params",
      details: { field: "url" },
    });
  });

  it("surfaces transport failures", async () => {
    const bridge: WorkerBridge = {
      isPreview: false,
      async request() {
        throw new Error("pipe closed");
      },
      async onEvent() {
        return () => undefined;
      },
    };
    const client = new WorkerClient(bridge);
    await expect(client.health()).rejects.toMatchObject({
      code: "transport.unavailable",
    });
  });
});
