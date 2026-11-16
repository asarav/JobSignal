import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";
import type { Envelope } from "../types/generated";

export interface WorkerBridge {
  /** True when the in-browser preview bridge is active instead of Tauri. */
  readonly isPreview: boolean;
  request(method: string, params: Record<string, unknown>): Promise<Envelope>;
  onEvent(handler: (event: Envelope) => void): Promise<UnlistenFn>;
}

function isTauriRuntime(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

class TauriBridge implements WorkerBridge {
  readonly isPreview = false;

  async request(method: string, params: Record<string, unknown>): Promise<Envelope> {
    return invoke<Envelope>("worker_request", { method, params });
  }

  onEvent(handler: (event: Envelope) => void): Promise<UnlistenFn> {
    return listen<Envelope>("worker://event", (event) => handler(event.payload));
  }
}

/**
 * Browser-only development bridge.
 *
 * Lets `npm run dev` render the real screens without the Tauri shell. It is
 * never used inside the desktop app, and it is clearly labelled in the UI.
 */
class PreviewBridge implements WorkerBridge {
  readonly isPreview = true;
  private tasks: Array<Record<string, unknown>> = [];
  private listeners: Array<(event: Envelope) => void> = [];

  async request(method: string, params: Record<string, unknown>): Promise<Envelope> {
    const response = (result: unknown): Envelope => ({
      v: 1,
      type: "response",
      id: "preview",
      params: {},
      ok: true,
      result: result as Record<string, unknown>,
    });

    switch (method) {
      case "system.hello":
        return response({
          protocol_version: 1,
          app_version: "0.0.0-preview",
          schema_version: 1,
          plugin_api_version: "1",
          capabilities: [
            "system.hello",
            "system.health",
            "system.shutdown",
            "tasks.list",
            "tasks.cancel",
            "settings.get",
            "settings.update",
            "plugins.list",
            "plugins.enable",
            "plugins.disable",
            "plugins.configure",
            "plugins.get_capabilities",
            "plugins.reset_circuit",
            "diagnostics.sleep",
            "diagnostics.fetch_url",
          ],
        });
      case "system.health":
        return response({
          status: "preview",
          app_version: "0.0.0-preview",
          schema_version: 1,
          protocol_version: 1,
          data_dir: "Preview mode (no Tauri bridge)",
          disk_usage_bytes: 0,
          plugin_health: previewPlugins(),
          queue_depth: this.tasks.filter((t) => t.status === "running").length,
        });
      case "settings.get":
      case "settings.update":
        return response({
          config: previewConfig(),
          overrides: {},
          schema_version: 1,
        });
      case "plugins.list":
        return response({ plugins: previewPlugins() });
      case "sources.list":
        return response({ sources: [] });
      case "tasks.list":
        return response({ tasks: this.tasks });
      case "tasks.cancel":
        this.tasks = this.tasks.map((task) =>
          task.id === params.task_id ? { ...task, status: "cancelled" } : task,
        );
        return response({ ok: true });
      case "diagnostics.sleep": {
        const taskId = `preview-${this.tasks.length + 1}`;
        this.tasks = [
          {
            id: taskId,
            type: "diagnostics.sleep",
            status: "running",
            priority: 5,
            params,
            progress: { step: 1, total: 2 },
            error: null,
            created_at: new Date().toISOString(),
            started_at: new Date().toISOString(),
            finished_at: null,
            parent_task_id: null,
          },
          ...this.tasks,
        ];
        this.emit({
          v: 1,
          type: "event",
          params: {},
          topic: "task.progress",
          seq: 1,
          ts: new Date().toISOString(),
          task_id: taskId,
          data: { step: 1, total: 2 },
        });
        return response({ task_id: taskId });
      }
      default:
        return {
          v: 1,
          type: "response",
          id: "preview",
          params: {},
          ok: false,
          error: {
            code: "not_found",
            message: `preview bridge does not implement ${method}`,
            retryable: false,
            details: {},
          },
        };
    }
  }

  async onEvent(handler: (event: Envelope) => void): Promise<UnlistenFn> {
    this.listeners.push(handler);
    return () => {
      this.listeners = this.listeners.filter((listener) => listener !== handler);
    };
  }

  private emit(event: Envelope) {
    for (const listener of this.listeners) listener(event);
  }
}

function previewPlugins(): Array<Record<string, unknown>> {
  return [
    {
      plugin_id: "extract.demo",
      kind: "extraction",
      version: "1.0.0",
      api_version: "1",
      enabled: true,
      health: { state: "closed", consecutive_failures: 0 },
      capabilities: ["extract"],
      network_hosts: [],
      description: "Builtin demo extraction strategy (M0 smoke test; no real extraction).",
    },
  ];
}

function previewConfig(): Record<string, unknown> {
  return {
    app_version: "0.0.0-preview",
    protocol_version: 1,
    paths: { data_dir: "Preview mode (no Tauri bridge)", db_name: "lodestar.sqlite" },
    features: {
      search_engine_discovery: false,
      rendered_dom_extraction: false,
      local_embeddings: false,
      autofill: false,
      outcome_analytics: false,
      external_plugins: false,
    },
    logging: { level: "info", rotate_files: 10, rotate_mb: 5 },
  };
}

export const defaultBridge: WorkerBridge = isTauriRuntime()
  ? new TauriBridge()
  : new PreviewBridge();

export { PreviewBridge, TauriBridge, isTauriRuntime };
