import type { UnlistenFn } from "@tauri-apps/api/event";
import type {
  ApplicationsCreateRequest,
  BrowserAutofillPlanRequest,
  BrowserAutofillPlanResponse,
  BrowserAutofillRequest,
  BrowserAutofillResponse,
  BrowserCloseSessionRequest,
  BrowserCloseSessionResponse,
  BrowserHandoffRequest,
  BrowserHandoffResponse,
  BrowserInspectFormRequest,
  BrowserInspectFormResponse,
  BrowserOpenJobRequest,
  BrowserOpenJobResponse,
  ApplicationsCreateResponse,
  ApplicationsGetAnalyticsRequest,
  ApplicationsGetAnalyticsResponse,
  ApplicationsGetCompanyHistoryRequest,
  ApplicationsGetCompanyHistoryResponse,
  ApplicationsGetSuppressionPolicyRequest,
  ApplicationsGetSuppressionPolicyResponse,
  ApplicationsGetSuppressionsRequest,
  ApplicationsGetSuppressionsResponse,
  ApplicationsRecordOutcomeRequest,
  ApplicationsRecordOutcomeResponse,
  ApplicationsListRequest,
  ApplicationsListResponse,
  ApplicationsRestoreSuppressedJobRequest,
  ApplicationsRestoreSuppressedJobResponse,
  ApplicationsUpdateStatusRequest,
  ApplicationsUpdateStatusResponse,
  ApplicationsUpdateSuppressionPolicyRequest,
  ApplicationsUpdateSuppressionPolicyResponse,
  CompaniesGetDetailRequest,
  CompaniesGetMergeHistoryRequest,
  CompaniesGetMergeHistoryResponse,
  CompaniesMergeRequest,
  CompaniesMergeResponse,
  CompaniesUnmergeRequest,
  CompaniesGetDetailResponse,
  CompaniesListRequest,
  CompaniesListResponse,
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
  KeywordsBackfillRequest,
  KeywordsBackfillResponse,
  KeywordsCoverageRequest,
  KeywordsCoverageResponse,
  KeywordsExtractRequest,
  KeywordsPrevalenceRequest,
  KeywordsTrendsRequest,
  KeywordsTrendsResponse,
  KeywordsPrevalenceResponse,
  KeywordsExtractResponse,
  MatchingRunRequest,
  MatchingRunResponse,
  MatchingApplyWeightProposalRequest,
  MatchingApplyWeightProposalResponse,
  MatchingGetResultsRequest,
  MatchingGetWeightHistoryRequest,
  MatchingGetWeightHistoryResponse,
  MatchingGetWeightProposalsRequest,
  MatchingGetWeightProposalsResponse,
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
  JobsListSavedRequest,
  JobsListSavedResponse,
  JobsListSeenRequest,
  JobsListSeenResponse,
  JobsRankedRequest,
  JobsRankedResponse,
  JobsVerifyNowRequest,
  JobsVerifyNowResponse,
  PostingIntelligenceConfigureModelRequest,
  PostingIntelligenceConfigureModelResponse,
  PostingIntelligenceEvaluateRequest,
  PostingIntelligenceEvaluateResponse,
  PostingIntelligenceGetCalibrationRequest,
  PostingIntelligenceGetCalibrationResponse,
  PostingIntelligenceGetCompanyAssessmentRequest,
  PostingIntelligenceGetCompanyAssessmentResponse,
  PostingIntelligenceGetRiskHistoryRequest,
  PostingIntelligenceGetRiskHistoryResponse,
  PostingIntelligenceGetSignalsRequest,
  PostingIntelligenceGetSignalsResponse,
  JobsSearchRequest,
  JobsSetUserStateRequest,
  JobsSetUserStateResponse,
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
  SystemExportBackupRequest,
  SystemExportBackupResponse,
  SystemGetBackupHistoryRequest,
  SystemGetBackupHistoryResponse,
  SystemHealthRequest,
  SystemImportBackupRequest,
  SystemImportBackupResponse,
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
  | "system.export_backup"
  | "system.import_backup"
  | "system.get_backup_history"
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
  | "jobs.ranked"
  | "jobs.list_saved"
  | "jobs.verify_now"
  | "posting_intelligence.evaluate"
  | "posting_intelligence.get_signals"
  | "posting_intelligence.get_risk_history"
  | "posting_intelligence.get_company_assessment"
  | "posting_intelligence.configure_model"
  | "posting_intelligence.get_calibration"
  | "crawl_runs.list"
  | "search_profiles.list"
  | "search_profiles.save"
  | "search_profiles.delete"
  | "discovery.run"
  | "discovery.auto_discover"
  | "applications.list"
  | "applications.create"
  | "applications.update_status"
  | "applications.add_event"
  | "applications.record_outcome"
  | "applications.get_company_history"
  | "applications.get_analytics"
  | "companies.list"
  | "companies.get_detail"
  | "companies.merge"
  | "companies.unmerge"
  | "companies.get_merge_history"
  | "applications.get_suppressions"
  | "applications.get_suppression_policy"
  | "applications.restore_suppressed_job"
  | "applications.update_suppression_policy"
  | "jobs.set_user_state"
  | "browser.open_job"
  | "browser.inspect_form"
  | "browser.autofill_plan"
  | "browser.autofill"
  | "browser.handoff"
  | "browser.close_session"
  | "resume.import"
  | "resume.list_versions"
  | "resume.get_profile"
  | "resume.set_active"
  | "resume.update_overrides"
  | "resume.parse"
  | "resume.set_skill_status"
  | "keywords.extract"
  | "keywords.prevalence"
  | "keywords.coverage"
  | "keywords.backfill"
  | "keywords.trends"
  | "matching.get_weight_proposals"
  | "matching.apply_weight_proposal"
  | "matching.get_weight_history"
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

  exportBackup(
    request: SystemExportBackupRequest = {},
  ): Promise<SystemExportBackupResponse> {
    return this.request("system.export_backup", { ...request });
  }

  importBackup(
    request: SystemImportBackupRequest,
  ): Promise<SystemImportBackupResponse> {
    return this.request("system.import_backup", { ...request });
  }

  getBackupHistory(
    request: SystemGetBackupHistoryRequest = {},
  ): Promise<SystemGetBackupHistoryResponse> {
    return this.request("system.get_backup_history", { ...request });
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

  rankedJobs(request: JobsRankedRequest = {}): Promise<JobsRankedResponse> {
    return this.request("jobs.ranked", { ...request });
  }

  listSavedJobs(request: JobsListSavedRequest = {}): Promise<JobsListSavedResponse> {
    return this.request("jobs.list_saved", { ...request });
  }

  verifyJobNow(request: JobsVerifyNowRequest): Promise<JobsVerifyNowResponse> {
    return this.request("jobs.verify_now", { ...request });
  }

  evaluatePostingIntelligence(
    request: PostingIntelligenceEvaluateRequest = {},
  ): Promise<PostingIntelligenceEvaluateResponse> {
    return this.request("posting_intelligence.evaluate", { ...request });
  }

  getPostingSignals(
    request: PostingIntelligenceGetSignalsRequest,
  ): Promise<PostingIntelligenceGetSignalsResponse> {
    return this.request("posting_intelligence.get_signals", { ...request });
  }

  getRiskHistory(
    request: PostingIntelligenceGetRiskHistoryRequest,
  ): Promise<PostingIntelligenceGetRiskHistoryResponse> {
    return this.request("posting_intelligence.get_risk_history", { ...request });
  }

  getCompanyAssessment(
    request: PostingIntelligenceGetCompanyAssessmentRequest,
  ): Promise<PostingIntelligenceGetCompanyAssessmentResponse> {
    return this.request("posting_intelligence.get_company_assessment", { ...request });
  }

  configurePostingIntelligence(
    request: PostingIntelligenceConfigureModelRequest,
  ): Promise<PostingIntelligenceConfigureModelResponse> {
    return this.request("posting_intelligence.configure_model", { ...request });
  }

  listCompanies(request: CompaniesListRequest = {}): Promise<CompaniesListResponse> {
    return this.request("companies.list", { ...request });
  }

  getCompanyDetail(
    request: CompaniesGetDetailRequest,
  ): Promise<CompaniesGetDetailResponse> {
    return this.request("companies.get_detail", { ...request });
  }

  mergeCompany(request: CompaniesMergeRequest): Promise<CompaniesMergeResponse> {
    return this.request("companies.merge", { ...request });
  }

  unmergeCompany(request: CompaniesUnmergeRequest): Promise<CompaniesMergeResponse> {
    return this.request("companies.unmerge", { ...request });
  }

  getCompanyMergeHistory(
    request: CompaniesGetMergeHistoryRequest = {},
  ): Promise<CompaniesGetMergeHistoryResponse> {
    return this.request("companies.get_merge_history", { ...request });
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

  getKeywordsPrevalence(
    request: KeywordsPrevalenceRequest = {},
  ): Promise<KeywordsPrevalenceResponse> {
    return this.request("keywords.prevalence", { ...request });
  }

  getKeywordsCoverage(
    request: KeywordsCoverageRequest = {},
  ): Promise<KeywordsCoverageResponse> {
    return this.request("keywords.coverage", { ...request });
  }

  backfillKeywords(request: KeywordsBackfillRequest = {}): Promise<KeywordsBackfillResponse> {
    return this.request("keywords.backfill", { ...request });
  }

  getKeywordTrends(
    request: KeywordsTrendsRequest = {},
  ): Promise<KeywordsTrendsResponse> {
    return this.request("keywords.trends", { ...request });
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

  getWeightProposals(
    request: MatchingGetWeightProposalsRequest = {},
  ): Promise<MatchingGetWeightProposalsResponse> {
    return this.request("matching.get_weight_proposals", { ...request });
  }

  applyWeightProposal(
    request: MatchingApplyWeightProposalRequest,
  ): Promise<MatchingApplyWeightProposalResponse> {
    return this.request("matching.apply_weight_proposal", { ...request });
  }

  getWeightHistory(
    request: MatchingGetWeightHistoryRequest = {},
  ): Promise<MatchingGetWeightHistoryResponse> {
    return this.request("matching.get_weight_history", { ...request });
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

  listApplications(
    request: ApplicationsListRequest = {},
  ): Promise<ApplicationsListResponse> {
    return this.request("applications.list", { ...request });
  }

  createApplication(
    request: ApplicationsCreateRequest,
  ): Promise<ApplicationsCreateResponse> {
    return this.request("applications.create", { ...request });
  }

  updateApplicationStatus(
    request: ApplicationsUpdateStatusRequest,
  ): Promise<ApplicationsUpdateStatusResponse> {
    return this.request("applications.update_status", { ...request });
  }

  getCompanyHistory(
    request: ApplicationsGetCompanyHistoryRequest,
  ): Promise<ApplicationsGetCompanyHistoryResponse> {
    return this.request("applications.get_company_history", { ...request });
  }

  recordApplicationOutcome(
    request: ApplicationsRecordOutcomeRequest,
  ): Promise<ApplicationsRecordOutcomeResponse> {
    return this.request("applications.record_outcome", { ...request });
  }

  getAnalytics(
    request: ApplicationsGetAnalyticsRequest = {},
  ): Promise<ApplicationsGetAnalyticsResponse> {
    return this.request("applications.get_analytics", { ...request });
  }

  getCalibration(
    request: PostingIntelligenceGetCalibrationRequest = {},
  ): Promise<PostingIntelligenceGetCalibrationResponse> {
    return this.request("posting_intelligence.get_calibration", { ...request });
  }

  getSuppressions(
    request: ApplicationsGetSuppressionsRequest = {},
  ): Promise<ApplicationsGetSuppressionsResponse> {
    return this.request("applications.get_suppressions", { ...request });
  }

  getSuppressionPolicy(
    request: ApplicationsGetSuppressionPolicyRequest = {},
  ): Promise<ApplicationsGetSuppressionPolicyResponse> {
    return this.request("applications.get_suppression_policy", { ...request });
  }

  restoreSuppressedJob(
    request: ApplicationsRestoreSuppressedJobRequest,
  ): Promise<ApplicationsRestoreSuppressedJobResponse> {
    return this.request("applications.restore_suppressed_job", { ...request });
  }

  updateSuppressionPolicy(
    request: ApplicationsUpdateSuppressionPolicyRequest,
  ): Promise<ApplicationsUpdateSuppressionPolicyResponse> {
    return this.request("applications.update_suppression_policy", { ...request });
  }

  setJobUserState(request: JobsSetUserStateRequest): Promise<JobsSetUserStateResponse> {
    return this.request("jobs.set_user_state", { ...request });
  }

  openJobInBrowser(request: BrowserOpenJobRequest): Promise<BrowserOpenJobResponse> {
    return this.request("browser.open_job", { ...request });
  }

  inspectApplicationForm(
    request: BrowserInspectFormRequest,
  ): Promise<BrowserInspectFormResponse> {
    return this.request("browser.inspect_form", { ...request });
  }

  planAutofill(request: BrowserAutofillPlanRequest): Promise<BrowserAutofillPlanResponse> {
    return this.request("browser.autofill_plan", { ...request });
  }

  autofillForm(request: BrowserAutofillRequest): Promise<BrowserAutofillResponse> {
    return this.request("browser.autofill", { ...request });
  }

  /** Turn the submission interceptor off because the user is submitting now. */
  handoffBrowserSession(
    request: BrowserHandoffRequest,
  ): Promise<BrowserHandoffResponse> {
    return this.request("browser.handoff", { ...request });
  }

  closeBrowserSession(
    request: BrowserCloseSessionRequest,
  ): Promise<BrowserCloseSessionResponse> {
    return this.request("browser.close_session", { ...request });
  }

  onEvent(handler: (event: Envelope) => void): Promise<UnlistenFn> {
    return this.bridge.onEvent(handler);
  }
}

export const workerClient = new WorkerClient();
