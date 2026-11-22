import type { UnlistenFn } from "@tauri-apps/api/event";
import type {
  CrawlRunsListRequest,
  CrawlRunsListResponse,
  DiagnosticsFetchUrlRequest,
  DiagnosticsFetchUrlResponse,
  DiagnosticsSleepRequest,
  DiagnosticsSleepResponse,
  DiscoveryAutoDiscoverRequest,
  DiscoveryAutoDiscoverResponse,
  DiscoveryRunRequest,
  DiscoveryRunResponse,
  Envelope,
  KeywordsExtractRequest,
  KeywordsExtractResponse,
  MatchingRunRequest,
  MatchingRunResponse,
  MatchingGetResultsRequest,
  MatchingGetResultsResponse,
  MatchingUpdateWeightsRequest,
  MatchingUpdateWeightsResponse,
  ResumeImportRequest,
  ResumeImportResponse,
  ResumeParseRequest,
  ResumeParseResponse,
  ResumeSetSkillStatusRequest,
  ResumeSetSkillStatusResponse,
  ResumeListVersionsRequest,
  ResumeListVersionsResponse,
  ResumeGetProfileRequest,
  ResumeGetProfileResponse,
  ResumeSetActiveRequest,
  ResumeSetActiveResponse,
  ResumeUpdateOverridesRequest,
  ResumeUpdateOverridesResponse,
  JobsGetDetailRequest,
  JobsGetDetailResponse,
  JobsListRequest,
  JobsListResponse,
  JobsListSeenRequest,
  JobsListSeenResponse,
  JobsSearchRequest,
  JobsSearchResponse,
  JobsUnmergeRequest,
  JobsUnmergeResponse,
  PluginsConfigureRequest,
  PluginsConfigureResponse,
  PluginsDisableRequest,
  PluginsEnableRequest,
  PluginsGetCapabilitiesRequest,
  PluginsGetCapabilitiesResponse,
  PluginsListRequest,
  PluginsListResponse,
  PluginsResetCircuitRequest,
  SearchProfilesDeleteRequest,
  SearchProfilesListRequest,
  SearchProfilesListResponse,
  SearchProfilesSaveRequest,
  SearchProfilesSaveResponse,
  SettingsGetRequest,
  SettingsGetResponse,
  SettingsUpdateRequest,
  SettingsUpdateResponse,
  SourcesAddRequest,
  SourcesAddResponse,
  SourcesDeleteRequest,
  SourcesDeleteResponse,
  SourcesListRequest,
  SourcesListResponse,
  SourcesSetEnabledRequest,
  SourcesSetEnabledResponse,
  SourcesSetExtractionOrderRequest,
  SourcesSetExtractionOrderResponse,
  SourcesSetScheduleRequest,
  SourcesSetScheduleResponse,
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
  | "diagnostics.fetch_url"
  | "sources.list"
  | "sources.add"
  | "sources.set_enabled"
  | "sources.set_extraction_order"
  | "sources.set_schedule"
  | "sources.delete"
  | "jobs.list"
  | "jobs.search"
  | "jobs.merge"
  | "jobs.unmerge"
  | "jobs.list_duplicates"
  | "jobs.list_seen"
  | "jobs.get_detail"
  | "crawl_runs.list"
  | "search_profiles.list"
  | "search_profiles.save"
  | "search_profiles.delete"
  | "discovery.run"
  | "discovery.auto_discover"
  | "resume.import"
  | "resume.list_versions"
  | "resume.get_profile"
  | "resume.set_active"
  | "resume.update_overrides"
  | "resume.parse"
  | "resume.set_skill_status"
  | "keywords.extract"
  | "matching.run"
  | "matching.get_results"
  | "matching.update_weights";

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

  listSources(request: SourcesListRequest = {}): Promise<SourcesListResponse> {
    return this.request("sources.list", { ...request });
  }

  addSource(request: SourcesAddRequest): Promise<SourcesAddResponse> {
    return this.request("sources.add", { ...request });
  }

  setSourceExtractionOrder(request: SourcesSetExtractionOrderRequest): Promise<SourcesSetExtractionOrderResponse> {
    return this.request("sources.set_extraction_order", { ...request });
  }

  setSourceEnabled(request: SourcesSetEnabledRequest): Promise<SourcesSetEnabledResponse> {
    return this.request("sources.set_enabled", { ...request });
  }

  setSourceSchedule(request: SourcesSetScheduleRequest): Promise<SourcesSetScheduleResponse> {
    return this.request("sources.set_schedule", { ...request });
  }

  deleteSource(request: SourcesDeleteRequest): Promise<SourcesDeleteResponse> {
    return this.request("sources.delete", { ...request });
  }

  listJobs(request: JobsListRequest = {}): Promise<JobsListResponse> {
    return this.request("jobs.list", { ...request });
  }

  searchJobs(request: JobsSearchRequest): Promise<JobsSearchResponse> {
    return this.request("jobs.search", { ...request });
  }

  listSeenJobs(request: JobsListSeenRequest = {}): Promise<JobsListSeenResponse> {
    return this.request("jobs.list_seen", { ...request });
  }

  getJobDetail(request: JobsGetDetailRequest): Promise<JobsGetDetailResponse> {
    return this.request("jobs.get_detail", { ...request });
  }

  unmergeJobs(request: JobsUnmergeRequest): Promise<JobsUnmergeResponse> {
    return this.request("jobs.unmerge", { ...request });
  }

  listCrawlRuns(request: CrawlRunsListRequest = {}): Promise<CrawlRunsListResponse> {
    return this.request("crawl_runs.list", { ...request });
  }

  listSearchProfiles(request: SearchProfilesListRequest = {}): Promise<SearchProfilesListResponse> {
    return this.request("search_profiles.list", { ...request });
  }

  saveSearchProfile(request: SearchProfilesSaveRequest): Promise<SearchProfilesSaveResponse> {
    return this.request("search_profiles.save", { ...request });
  }

  deleteSearchProfile(request: SearchProfilesDeleteRequest): Promise<{ ok: boolean }> {
    return this.request("search_profiles.delete", { ...request });
  }

  runDiscovery(request: DiscoveryRunRequest): Promise<DiscoveryRunResponse> {
    return this.request("discovery.run", { ...request });
  }

  autoDiscover(request: DiscoveryAutoDiscoverRequest = {}): Promise<DiscoveryAutoDiscoverResponse> {
    return this.request("discovery.auto_discover", { ...request });
  }

  importResume(request: ResumeImportRequest): Promise<ResumeImportResponse> {
    return this.request("resume.import", { ...request });
  }
  listResumeVersions(request: ResumeListVersionsRequest = {}): Promise<ResumeListVersionsResponse> {
    return this.request("resume.list_versions", { ...request });
  }
  getResumeProfile(request: ResumeGetProfileRequest = {}): Promise<ResumeGetProfileResponse> {
    return this.request("resume.get_profile", { ...request });
  }
  setResumeActive(request: ResumeSetActiveRequest): Promise<ResumeSetActiveResponse> {
    return this.request("resume.set_active", { ...request });
  }
  updateResumeOverrides(request: ResumeUpdateOverridesRequest): Promise<ResumeUpdateOverridesResponse> {
    return this.request("resume.update_overrides", { ...request });
  }
  parseResume(request: ResumeParseRequest): Promise<ResumeParseResponse> {
    return this.request("resume.parse", { ...request });
  }
  setResumeSkillStatus(request: ResumeSetSkillStatusRequest): Promise<ResumeSetSkillStatusResponse> {
    return this.request("resume.set_skill_status", { ...request });
  }
  extractKeywords(request: KeywordsExtractRequest): Promise<KeywordsExtractResponse> {
    return this.request("keywords.extract", { ...request });
  }
  runMatching(request: MatchingRunRequest = {}): Promise<MatchingRunResponse> {
    return this.request("matching.run", { ...request });
  }
  getMatchingResults(request: MatchingGetResultsRequest = {}): Promise<MatchingGetResultsResponse> {
    return this.request("matching.get_results", { ...request });
  }
  updateMatchingWeights(request: MatchingUpdateWeightsRequest): Promise<MatchingUpdateWeightsResponse> {
    return this.request("matching.update_weights", { ...request });
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
