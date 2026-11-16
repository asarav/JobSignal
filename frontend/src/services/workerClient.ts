import type { UnlistenFn } from "@tauri-apps/api/event";
import type {
  DiagnosticsFetchUrlRequest,
  DiagnosticsFetchUrlResponse,
  DiagnosticsSleepRequest,
  DiagnosticsSleepResponse,
  Envelope,
  PluginsConfigureRequest,
  PluginsConfigureResponse,
  PluginsDisableRequest,
  PluginsEnableRequest,
  PluginsGetCapabilitiesRequest,
  PluginsGetCapabilitiesResponse,
  PluginsListRequest,
  PluginsListResponse,
  PluginsResetCircuitRequest,
  SettingsGetRequest,
  SettingsGetResponse,
  SettingsUpdateRequest,
  SettingsUpdateResponse,
  SystemHealthRequest,
  SystemHealthResponse,
  SystemHelloRequest,
  SystemHelloResponse,
  SystemShutdownRequest,
  SystemShutdownResponse,
  TasksCancelRequest,
  TasksCancelResponse,
  TasksListRequest,
  TasksListResponse,
} from "../types/generated";
import { defaultBridge, type WorkerBridge } from "./workerBridge";

export type WorkerMethod =
  | "system.hello"
  | "system.health"
  | "system.shutdown"
  | "tasks.list"
  | "tasks.cancel"
  | "settings.get"
  | "settings.update"
  | "plugins.list"
  | "plugins.enable"
  | "plugins.disable"
  | "plugins.configure"
  | "plugins.get_capabilities"
  | "plugins.reset_circuit"
  | "diagnostics.sleep"
  | "diagnostics.fetch_url";

export class WorkerError extends Error {
  readonly code: string;
  readonly retryable: boolean;
  readonly details: Record<string, unknown>;

  constructor(code: string, message: string, retryable = false, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "WorkerError";
    this.code = code;
    this.retryable = retryable;
    this.details = details;
  }
}

export class WorkerClient {
  constructor(private readonly bridge: WorkerBridge = defaultBridge) {}

  get isPreview(): boolean {
    return this.bridge.isPreview;
  }

  async request<T>(method: WorkerMethod, params: Record<string, unknown> = {}): Promise<T> {
    let envelope: Envelope;
    try {
      envelope = await this.bridge.request(method, params);
    } catch (error) {
      throw new WorkerError(
        "transport.unavailable",
        error instanceof Error ? error.message : String(error),
        true,
      );
    }
    if (envelope.type !== "response") {
      throw new WorkerError("internal.unexpected", "worker returned a non-response envelope");
    }
    if (!envelope.ok) {
      const details = (envelope.error ?? {}) as {
        code?: string;
        message?: string;
        retryable?: boolean;
        details?: Record<string, unknown>;
      };
      throw new WorkerError(
        details.code ?? "internal.unexpected",
        details.message ?? "worker request failed",
        Boolean(details.retryable),
        details.details ?? {},
      );
    }
    return envelope.result as T;
  }

  hello(request: SystemHelloRequest): Promise<SystemHelloResponse> {
    return this.request("system.hello", { ...request });
  }

  health(request: SystemHealthRequest = {}): Promise<SystemHealthResponse> {
    return this.request("system.health", { ...request });
  }

  shutdown(request: SystemShutdownRequest = {}): Promise<SystemShutdownResponse> {
    return this.request("system.shutdown", { ...request });
  }

  listTasks(request: TasksListRequest = {}): Promise<TasksListResponse> {
    return this.request("tasks.list", { ...request });
  }

  cancelTask(request: TasksCancelRequest): Promise<TasksCancelResponse> {
    return this.request("tasks.cancel", { ...request });
  }

  getSettings(request: SettingsGetRequest = {}): Promise<SettingsGetResponse> {
    return this.request("settings.get", { ...request });
  }

  updateSettings(request: SettingsUpdateRequest): Promise<SettingsUpdateResponse> {
    return this.request("settings.update", { ...request });
  }

  listPlugins(request: PluginsListRequest = {}): Promise<PluginsListResponse> {
    return this.request("plugins.list", { ...request });
  }

  enablePlugin(request: PluginsEnableRequest): Promise<{ ok: boolean }> {
    return this.request("plugins.enable", { ...request });
  }

  disablePlugin(request: PluginsDisableRequest): Promise<{ ok: boolean }> {
    return this.request("plugins.disable", { ...request });
  }

  configurePlugin(request: PluginsConfigureRequest): Promise<PluginsConfigureResponse> {
    return this.request("plugins.configure", { ...request });
  }

  getCapabilities(
    request: PluginsGetCapabilitiesRequest = {},
  ): Promise<PluginsGetCapabilitiesResponse> {
    return this.request("plugins.get_capabilities", { ...request });
  }

  resetCircuit(request: PluginsResetCircuitRequest): Promise<{ ok: boolean }> {
    return this.request("plugins.reset_circuit", { ...request });
  }

  startSleepTask(request: DiagnosticsSleepRequest = {}): Promise<DiagnosticsSleepResponse> {
    return this.request("diagnostics.sleep", { ...request });
  }

  fetchUrl(request: DiagnosticsFetchUrlRequest): Promise<DiagnosticsFetchUrlResponse> {
    return this.request("diagnostics.fetch_url", { ...request });
  }

  onEvent(handler: (event: Envelope) => void): Promise<UnlistenFn> {
    return this.bridge.onEvent(handler);
  }
}

export const workerClient = new WorkerClient();
