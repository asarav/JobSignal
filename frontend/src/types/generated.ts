// AUTO-GENERATED from schemas/ipc/**/*.json. DO NOT EDIT BY HAND.
// Regenerate with: node scripts/gen-ts-types.mjs
// Source of truth: Pydantic models in app/worker/ipc/method_catalog.py
// CI fails if this file is stale.

// schemas/ipc/diagnostics/diagnostics_fetch_url_request.json
export interface DiagnosticsFetchUrlRequest {
  url: string;
}

// schemas/ipc/diagnostics/diagnostics_fetch_url_response.json
export interface DiagnosticsFetchUrlResponse {
  status: number;
  url: string;
  elapsed_ms: number;
  bytes: number;
}

// schemas/ipc/diagnostics/diagnostics_sleep_request.json
export interface DiagnosticsSleepRequest {
  seconds?: number;
  steps?: number;
}

// schemas/ipc/diagnostics/diagnostics_sleep_response.json
export interface DiagnosticsSleepResponse {
  task_id: string;
}

// schemas/ipc/envelope.json
/**
 * Wire envelope union, kept narrow for M0 consumers.
 */
export interface Envelope {
  v?: 1;
  type: "request" | "response" | "event";
  id?: string | null;
  method?: string | null;
  params?: {
    [k: string]: unknown;
  };
  meta?: {
    [k: string]: unknown;
  };
  ok?: boolean | null;
  result?: {
    [k: string]: unknown;
  };
  error?: {
    [k: string]: unknown;
  } | null;
  topic?: string | null;
  seq?: number;
  ts?: string;
  task_id?: string | null;
  data?: {
    [k: string]: unknown;
  };
}

// schemas/ipc/event/worker_ready.json
export interface WorkerReadyEvent {
  v?: 1;
  type?: "event";
  topic?: "worker.ready";
  seq?: number;
  ts?: string;
  data: WorkerReadyEventWorkerReadyData;
}
export interface WorkerReadyEventWorkerReadyData {
  protocol_versions: number[];
  app_version: string;
  schema_version: number;
}

// schemas/ipc/plugins/plugins_configure_request.json
export interface PluginsConfigureRequest {
  plugin_id: string;
  config?: {
    [k: string]: unknown;
  };
}

// schemas/ipc/plugins/plugins_configure_response.json
export interface PluginsConfigureResponse {
  config: {
    [k: string]: unknown;
  };
}

// schemas/ipc/plugins/plugins_disable_request.json
export interface PluginsDisableRequest {
  plugin_id: string;
}

// schemas/ipc/plugins/plugins_disable_response.json
export interface PluginsDisableResponse {
  ok?: boolean;
}

// schemas/ipc/plugins/plugins_enable_request.json
export interface PluginsEnableRequest {
  plugin_id: string;
}

// schemas/ipc/plugins/plugins_enable_response.json
export interface PluginsEnableResponse {
  ok?: boolean;
}

// schemas/ipc/plugins/plugins_get_capabilities_request.json
export interface PluginsGetCapabilitiesRequest {
  plugin_id?: string | null;
}

// schemas/ipc/plugins/plugins_get_capabilities_response.json
export interface PluginsGetCapabilitiesResponse {
  capabilities: {
    [k: string]: string[];
  };
}

// schemas/ipc/plugins/plugins_list_request.json
export interface PluginsListRequest {}

// schemas/ipc/plugins/plugins_list_response.json
export interface PluginsListResponse {
  plugins: PluginsListResponsePluginInfo[];
}
export interface PluginsListResponsePluginInfo {
  plugin_id: string;
  kind: string;
  version: string;
  api_version: string;
  enabled: boolean;
  health: {
    [k: string]: unknown;
  };
  capabilities: string[];
  network_hosts: string[];
  description: string;
}

// schemas/ipc/plugins/plugins_reset_circuit_request.json
export interface PluginsResetCircuitRequest {
  plugin_id: string;
}

// schemas/ipc/plugins/plugins_reset_circuit_response.json
export interface PluginsResetCircuitResponse {
  ok?: boolean;
}

// schemas/ipc/settings/settings_get_request.json
export interface SettingsGetRequest {}

// schemas/ipc/settings/settings_get_response.json
export interface SettingsGetResponse {
  config: {
    [k: string]: unknown;
  };
  overrides: {
    [k: string]: unknown;
  };
  schema_version: number;
}

// schemas/ipc/settings/settings_update_request.json
export interface SettingsUpdateRequest {
  patch: {
    [k: string]: unknown;
  };
}

// schemas/ipc/settings/settings_update_response.json
export interface SettingsUpdateResponse {
  config: {
    [k: string]: unknown;
  };
  overrides: {
    [k: string]: unknown;
  };
  schema_version: number;
}

// schemas/ipc/system/system_health_request.json
export interface SystemHealthRequest {}

// schemas/ipc/system/system_health_response.json
export interface SystemHealthResponse {
  status: string;
  app_version: string;
  schema_version: number;
  protocol_version: number;
  data_dir: string;
  disk_usage_bytes: number;
  plugin_health?: {
    [k: string]: unknown;
  }[];
  queue_depth?: number;
}

// schemas/ipc/system/system_hello_request.json
export interface SystemHelloRequest {
  protocol_version: number;
  ui_version: string;
}

// schemas/ipc/system/system_hello_response.json
export interface SystemHelloResponse {
  protocol_version: number;
  app_version: string;
  schema_version: number;
  plugin_api_version: string;
  capabilities: string[];
}

// schemas/ipc/system/system_shutdown_request.json
export interface SystemShutdownRequest {}

// schemas/ipc/system/system_shutdown_response.json
export interface SystemShutdownResponse {
  ok?: boolean;
}

// schemas/ipc/tasks/tasks_cancel_request.json
export interface TasksCancelRequest {
  task_id: string;
}

// schemas/ipc/tasks/tasks_cancel_response.json
export interface TasksCancelResponse {
  ok?: boolean;
}

// schemas/ipc/tasks/tasks_list_request.json
export interface TasksListRequest {
  status?: string | null;
  limit?: number;
}

// schemas/ipc/tasks/tasks_list_response.json
export interface TasksListResponse {
  tasks: TasksListResponseTaskSummary[];
}
export interface TasksListResponseTaskSummary {
  id: string;
  type: string;
  status: string;
  priority: number;
  params?: {
    [k: string]: unknown;
  };
  progress?: {
    [k: string]: unknown;
  };
  error?: {
    [k: string]: unknown;
  } | null;
  created_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  parent_task_id?: string | null;
}
