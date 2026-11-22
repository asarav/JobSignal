// AUTO-GENERATED from schemas/ipc/**/*.json. DO NOT EDIT BY HAND.
// Regenerate with: node scripts/gen-ts-types.mjs
// Source of truth: Pydantic models in app/worker/ipc/method_catalog.py
// CI fails if this file is stale.

// schemas/ipc/applications/applications_add_event_request.json
export interface ApplicationsAddEventRequest {
  application_id: string;
  kind?: string;
  note?: string;
  data?: {
    [k: string]: unknown;
  };
}

// schemas/ipc/applications/applications_add_event_response.json
export interface ApplicationsAddEventResponse {
  ok?: boolean;
}

// schemas/ipc/applications/applications_create_request.json
export interface ApplicationsCreateRequest {
  job_id: string;
  status?: string;
  applied_at?: string | null;
  notes?: string;
  resume_version_id?: string | null;
}

// schemas/ipc/applications/applications_create_response.json
export interface ApplicationsCreateResponse {
  application_id: string;
  company_id: string;
  job_id: string;
  job_title: string;
  job_title_family?: string | null;
  status: string;
  applied_at?: string | null;
  resume_version_id?: string | null;
  notes?: string;
  created_at?: string | null;
  updated_at?: string | null;
  events?: {
    [k: string]: unknown;
  }[];
  outcomes?: {
    [k: string]: unknown;
  }[];
  suppressions?: {
    [k: string]: unknown;
  }[];
}

// schemas/ipc/applications/applications_get_company_history_request.json
export interface ApplicationsGetCompanyHistoryRequest {
  company_id: string;
}

// schemas/ipc/applications/applications_get_company_history_response.json
export interface ApplicationsGetCompanyHistoryResponse {
  company_id: string;
  applications: ApplicationsGetCompanyHistoryResponseApplicationInfo[];
  total?: number;
  total_applied?: number;
  last_applied_at?: string | null;
}
export interface ApplicationsGetCompanyHistoryResponseApplicationInfo {
  application_id: string;
  company_id: string;
  job_id: string;
  job_title: string;
  job_title_family?: string | null;
  status: string;
  applied_at?: string | null;
  resume_version_id?: string | null;
  notes?: string;
  created_at?: string | null;
  updated_at?: string | null;
  events?: {
    [k: string]: unknown;
  }[];
  outcomes?: {
    [k: string]: unknown;
  }[];
  suppressions?: {
    [k: string]: unknown;
  }[];
}

// schemas/ipc/applications/applications_get_suppression_policy_request.json
export interface ApplicationsGetSuppressionPolicyRequest {}

// schemas/ipc/applications/applications_get_suppression_policy_response.json
export interface ApplicationsGetSuppressionPolicyResponse {
  policy: {
    [k: string]: unknown;
  };
}

// schemas/ipc/applications/applications_get_suppressions_request.json
export interface ApplicationsGetSuppressionsRequest {}

// schemas/ipc/applications/applications_get_suppressions_response.json
export interface ApplicationsGetSuppressionsResponse {
  suppressions: ApplicationsGetSuppressionsResponseSuppressionInfo[];
  total?: number;
  policy?: {
    [k: string]: unknown;
  };
}
export interface ApplicationsGetSuppressionsResponseSuppressionInfo {
  suppression_id: string;
  job_id: string;
  application_id: string;
  job_title?: string | null;
  company_id?: string | null;
  state: string;
  reason: string;
  explanation: string;
  similarity?: number;
  expires_at?: string | null;
  created_at?: string | null;
  evidence?: {
    [k: string]: unknown;
  };
  policy?: {
    [k: string]: unknown;
  };
}

// schemas/ipc/applications/applications_list_request.json
export interface ApplicationsListRequest {}

// schemas/ipc/applications/applications_list_response.json
export interface ApplicationsListResponse {
  applications: ApplicationsListResponseApplicationInfo[];
  total?: number;
}
export interface ApplicationsListResponseApplicationInfo {
  application_id: string;
  company_id: string;
  job_id: string;
  job_title: string;
  job_title_family?: string | null;
  status: string;
  applied_at?: string | null;
  resume_version_id?: string | null;
  notes?: string;
  created_at?: string | null;
  updated_at?: string | null;
  events?: {
    [k: string]: unknown;
  }[];
  outcomes?: {
    [k: string]: unknown;
  }[];
  suppressions?: {
    [k: string]: unknown;
  }[];
}

// schemas/ipc/applications/applications_record_outcome_request.json
export interface ApplicationsRecordOutcomeRequest {
  application_id: string;
  kind: string;
  occurred_at?: string | null;
  note?: string;
}

// schemas/ipc/applications/applications_record_outcome_response.json
export interface ApplicationsRecordOutcomeResponse {
  ok?: boolean;
}

// schemas/ipc/applications/applications_restore_suppressed_job_request.json
export interface ApplicationsRestoreSuppressedJobRequest {
  job_id: string;
  application_id?: string | null;
}

// schemas/ipc/applications/applications_restore_suppressed_job_response.json
export interface ApplicationsRestoreSuppressedJobResponse {
  ok?: boolean;
}

// schemas/ipc/applications/applications_update_status_request.json
export interface ApplicationsUpdateStatusRequest {
  application_id: string;
  status: string;
  applied_at?: string | null;
  note?: string;
}

// schemas/ipc/applications/applications_update_status_response.json
export interface ApplicationsUpdateStatusResponse {
  application_id: string;
  company_id: string;
  job_id: string;
  job_title: string;
  job_title_family?: string | null;
  status: string;
  applied_at?: string | null;
  resume_version_id?: string | null;
  notes?: string;
  created_at?: string | null;
  updated_at?: string | null;
  events?: {
    [k: string]: unknown;
  }[];
  outcomes?: {
    [k: string]: unknown;
  }[];
  suppressions?: {
    [k: string]: unknown;
  }[];
}

// schemas/ipc/applications/applications_update_suppression_policy_request.json
export interface ApplicationsUpdateSuppressionPolicyRequest {
  policy?: {
    [k: string]: unknown;
  };
}

// schemas/ipc/applications/applications_update_suppression_policy_response.json
export interface ApplicationsUpdateSuppressionPolicyResponse {
  policy: {
    [k: string]: unknown;
  };
}

// schemas/ipc/browser/browser_autofill_plan_request.json
export interface BrowserAutofillPlanRequest {
  session_id: string;
}

// schemas/ipc/browser/browser_autofill_plan_response.json
export interface BrowserAutofillPlanResponse {
  plan_id: string;
  session_id: string;
  url?: string;
  step_index?: number;
  items?: {
    [k: string]: unknown;
  }[];
}

// schemas/ipc/browser/browser_autofill_request.json
export interface BrowserAutofillRequest {
  session_id: string;
  plan_id: string;
}

// schemas/ipc/browser/browser_autofill_response.json
export interface BrowserAutofillResponse {
  plan_id: string;
  filled?: {
    [k: string]: unknown;
  }[];
  skipped?: {
    [k: string]: unknown;
  }[];
  blocked?: {
    [k: string]: unknown;
  }[];
  errors?: string[];
  submitted?: boolean;
}

// schemas/ipc/browser/browser_close_session_request.json
export interface BrowserCloseSessionRequest {
  session_id: string;
}

// schemas/ipc/browser/browser_close_session_response.json
export interface BrowserCloseSessionResponse {
  ok?: boolean;
}

// schemas/ipc/browser/browser_handoff_request.json
export interface BrowserHandoffRequest {
  session_id: string;
}

// schemas/ipc/browser/browser_handoff_response.json
export interface BrowserHandoffResponse {
  session_id: string;
  interceptor_active?: boolean;
  submitted?: boolean;
}

// schemas/ipc/browser/browser_inspect_form_request.json
export interface BrowserInspectFormRequest {
  session_id: string;
}

// schemas/ipc/browser/browser_inspect_form_response.json
export interface BrowserInspectFormResponse {
  session_id: string;
  url: string;
  step_index?: number;
  step_count?: number;
  is_final_step?: boolean | null;
  fields?: {
    [k: string]: unknown;
  }[];
  controls?: {
    [k: string]: unknown;
  }[];
}

// schemas/ipc/browser/browser_open_job_request.json
export interface BrowserOpenJobRequest {
  job_id: string;
}

// schemas/ipc/browser/browser_open_job_response.json
export interface BrowserOpenJobResponse {
  session_id: string;
  url: string;
  job_id?: string;
}

// schemas/ipc/crawl_runs/crawl_runs_list_request.json
export interface CrawlRunsListRequest {}

// schemas/ipc/crawl_runs/crawl_runs_list_response.json
export interface CrawlRunsListResponse {
  runs: CrawlRunsListResponseCrawlRunSummary[];
}
export interface CrawlRunsListResponseCrawlRunSummary {
  run_id: string;
  source_id: string;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  stats?: {
    [k: string]: unknown;
  };
  error?: string | null;
}

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

// schemas/ipc/discovery/discovery_auto_discover_request.json
export interface DiscoveryAutoDiscoverRequest {}

// schemas/ipc/discovery/discovery_auto_discover_response.json
export interface DiscoveryAutoDiscoverResponse {
  discovered?: number;
  registered?: number;
  crawled?: number;
  failed?: number;
  postings_harvested?: number;
  postings_verified?: number;
  tiers_changed?: number;
}

// schemas/ipc/discovery/discovery_run_request.json
export interface DiscoveryRunRequest {
  source_id: string;
}

// schemas/ipc/discovery/discovery_run_response.json
export interface DiscoveryRunResponse {
  ok?: boolean;
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
  result?: Result;
  error?: {
    [k: string]: unknown;
  } | null;
  topic?: string | null;
  seq?: number;
  ts?: string;
  task_id?: string | null;
  data?: Data;
}
export interface Result {
  [k: string]: unknown;
}
export interface Data {
  [k: string]: unknown;
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

// schemas/ipc/jobs/jobs_get_detail_request.json
export interface JobsGetDetailRequest {
  job_id: string;
}

// schemas/ipc/jobs/jobs_get_detail_response.json
export interface JobsGetDetailResponse {
  job: {
    [k: string]: unknown;
  };
  snapshots?: {
    [k: string]: unknown;
  }[];
  observations?: {
    [k: string]: unknown;
  }[];
  relations?: {
    [k: string]: unknown;
  }[];
  reposts?: {
    [k: string]: unknown;
  }[];
}

// schemas/ipc/jobs/jobs_list_duplicates_request.json
export interface JobsListDuplicatesRequest {}

// schemas/ipc/jobs/jobs_list_duplicates_response.json
export interface JobsListDuplicatesResponse {
  jobs: JobsListDuplicatesResponseJobSummary[];
  total?: number;
}
export interface JobsListDuplicatesResponseJobSummary {
  job_id: string;
  title: string;
  company_id?: string | null;
  canonical_url?: string | null;
  posting_status?: string;
  source_id?: string | null;
  provenance?: string[];
  via_index?: boolean;
  reason?: string | null;
  related_job_ids?: string[];
}

// schemas/ipc/jobs/jobs_list_request.json
export interface JobsListRequest {
  source_id?: string | null;
  company_id?: string | null;
  query?: string | null;
  filters?: {
    [k: string]: unknown;
  };
  limit?: number;
  offset?: number;
}

// schemas/ipc/jobs/jobs_list_response.json
export interface JobsListResponse {
  jobs: JobsListResponseJobSummary[];
  total?: number;
}
export interface JobsListResponseJobSummary {
  job_id: string;
  title: string;
  company_id?: string | null;
  canonical_url?: string | null;
  posting_status?: string;
  source_id?: string | null;
  provenance?: string[];
  via_index?: boolean;
  reason?: string | null;
  related_job_ids?: string[];
}

// schemas/ipc/jobs/jobs_list_seen_request.json
export interface JobsListSeenRequest {}

// schemas/ipc/jobs/jobs_list_seen_response.json
export interface JobsListSeenResponse {
  jobs: JobsListSeenResponseJobSummary[];
  total?: number;
}
export interface JobsListSeenResponseJobSummary {
  job_id: string;
  title: string;
  company_id?: string | null;
  canonical_url?: string | null;
  posting_status?: string;
  source_id?: string | null;
  provenance?: string[];
  via_index?: boolean;
  reason?: string | null;
  related_job_ids?: string[];
}

// schemas/ipc/jobs/jobs_merge_request.json
export interface JobsMergeRequest {
  job_id: string;
  related_job_id: string;
}

// schemas/ipc/jobs/jobs_merge_response.json
export interface JobsMergeResponse {
  ok?: boolean;
}

// schemas/ipc/jobs/jobs_search_request.json
export interface JobsSearchRequest {
  query: string;
  limit?: number;
}

// schemas/ipc/jobs/jobs_search_response.json
export interface JobsSearchResponse {
  jobs: JobsSearchResponseJobSummary[];
  total?: number;
}
export interface JobsSearchResponseJobSummary {
  job_id: string;
  title: string;
  company_id?: string | null;
  canonical_url?: string | null;
  posting_status?: string;
  source_id?: string | null;
  provenance?: string[];
  via_index?: boolean;
  reason?: string | null;
  related_job_ids?: string[];
}

// schemas/ipc/jobs/jobs_set_user_state_request.json
export interface JobsSetUserStateRequest {
  job_id: string;
  state?: "new" | "saved" | "dismissed" | "hidden";
  note?: string | null;
}

// schemas/ipc/jobs/jobs_set_user_state_response.json
export interface JobsSetUserStateResponse {
  job_id: string;
  state: string;
  hidden?: boolean;
}

// schemas/ipc/jobs/jobs_unmerge_request.json
export interface JobsUnmergeRequest {
  job_id: string;
  related_job_id: string;
}

// schemas/ipc/jobs/jobs_unmerge_response.json
export interface JobsUnmergeResponse {
  ok?: boolean;
}

// schemas/ipc/keywords/keywords_extract_request.json
export interface KeywordsExtractRequest {
  text: string;
}

// schemas/ipc/keywords/keywords_extract_response.json
export interface KeywordsExtractResponse {
  keywords?: string[];
}

// schemas/ipc/matching/matching_get_results_request.json
export interface MatchingGetResultsRequest {
  profile_id?: string;
}

// schemas/ipc/matching/matching_get_results_response.json
export interface MatchingGetResultsResponse {
  results?: {
    [k: string]: unknown;
  }[];
  weights?: {
    [k: string]: number;
  };
}

// schemas/ipc/matching/matching_run_request.json
export interface MatchingRunRequest {
  profile_id?: string;
  job_ids?: string[];
}

// schemas/ipc/matching/matching_run_response.json
export interface MatchingRunResponse {
  results?: {
    [k: string]: unknown;
  }[];
}

// schemas/ipc/matching/matching_update_weights_request.json
export interface MatchingUpdateWeightsRequest {
  profile_id?: string;
  weights?: {
    [k: string]: unknown;
  };
}

// schemas/ipc/matching/matching_update_weights_response.json
export interface MatchingUpdateWeightsResponse {
  ok?: boolean;
  weights?: {
    [k: string]: number;
  };
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

// schemas/ipc/resume/resume_get_profile_request.json
export interface ResumeGetProfileRequest {
  profile_id?: string;
}

// schemas/ipc/resume/resume_get_profile_response.json
export interface ResumeGetProfileResponse {
  profile?: {
    [k: string]: unknown;
  } | null;
}

// schemas/ipc/resume/resume_import_request.json
export interface ResumeImportRequest {
  file_path: string;
  label?: string;
}

// schemas/ipc/resume/resume_import_response.json
export interface ResumeImportResponse {
  profile_id: string;
  version_id: string;
  skills?: string[];
  sections?: string[];
  contact?: {
    [k: string]: string;
  };
  years_experience?: number | null;
}

// schemas/ipc/resume/resume_list_versions_request.json
export interface ResumeListVersionsRequest {
  profile_id?: string;
}

// schemas/ipc/resume/resume_list_versions_response.json
export interface ResumeListVersionsResponse {
  versions?: {
    [k: string]: unknown;
  }[];
}

// schemas/ipc/resume/resume_parse_request.json
export interface ResumeParseRequest {
  resume_version_id: string;
}

// schemas/ipc/resume/resume_parse_response.json
export interface ResumeParseResponse {
  version_id: string;
  text: string;
  sections?: {
    [k: string]: string;
  };
  skills?: string[];
  years_experience?: number | null;
}

// schemas/ipc/resume/resume_set_active_request.json
export interface ResumeSetActiveRequest {
  profile_id?: string;
  version_id: string;
}

// schemas/ipc/resume/resume_set_active_response.json
export interface ResumeSetActiveResponse {
  ok?: boolean;
}

// schemas/ipc/resume/resume_set_skill_status_request.json
export interface ResumeSetSkillStatusRequest {
  profile_id?: string;
  keyword_id: string;
  status: "have_experience" | "learning" | "no_experience" | "not_relevant";
}

// schemas/ipc/resume/resume_set_skill_status_response.json
export interface ResumeSetSkillStatusResponse {
  ok?: boolean;
}

// schemas/ipc/resume/resume_update_overrides_request.json
export interface ResumeUpdateOverridesRequest {
  profile_id?: string;
  overrides?: {
    [k: string]: unknown;
  };
}

// schemas/ipc/resume/resume_update_overrides_response.json
export interface ResumeUpdateOverridesResponse {
  ok?: boolean;
}

// schemas/ipc/search_profiles/search_profiles_delete_request.json
export interface SearchProfilesDeleteRequest {
  profile_id: string;
}

// schemas/ipc/search_profiles/search_profiles_delete_response.json
export interface SearchProfilesDeleteResponse {
  ok?: boolean;
}

// schemas/ipc/search_profiles/search_profiles_list_request.json
export interface SearchProfilesListRequest {}

// schemas/ipc/search_profiles/search_profiles_list_response.json
export interface SearchProfilesListResponse {
  profiles: SearchProfilesListResponseSearchProfileInfo[];
}
export interface SearchProfilesListResponseSearchProfileInfo {
  profile_id: string;
  name: string;
  candidate_profile_id?: string;
  scoring_config_id?: string | null;
  ranking_policy?: string;
  hard_filters?: {
    [k: string]: unknown;
  };
  preferences?: {
    [k: string]: unknown;
  };
}

// schemas/ipc/search_profiles/search_profiles_save_request.json
export interface SearchProfilesSaveRequest {
  profile_id?: string | null;
  name: string;
  candidate_profile_id?: string;
  scoring_config_id?: string | null;
  ranking_policy?: string;
  hard_filters?: {
    [k: string]: unknown;
  };
  preferences?: {
    [k: string]: unknown;
  };
}

// schemas/ipc/search_profiles/search_profiles_save_response.json
export interface SearchProfilesSaveResponse {
  profile_id: string;
  name: string;
  candidate_profile_id?: string;
  scoring_config_id?: string | null;
  ranking_policy?: string;
  hard_filters?: {
    [k: string]: unknown;
  };
  preferences?: {
    [k: string]: unknown;
  };
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

// schemas/ipc/sources/sources_add_request.json
export interface SourcesAddRequest {
  url: string;
  connector_id?: string | null;
  company_id?: string | null;
  interval_s?: number;
}

// schemas/ipc/sources/sources_add_response.json
export interface SourcesAddResponse {
  source_id: string;
  url: string;
  kind: string;
  connector_id?: string | null;
  company_id?: string | null;
  enabled?: boolean;
  interval_s?: number;
  extraction_order?: string[];
  tier?: string | null;
  tier_reason?: string | null;
}

// schemas/ipc/sources/sources_delete_request.json
export interface SourcesDeleteRequest {
  source_id: string;
}

// schemas/ipc/sources/sources_delete_response.json
export interface SourcesDeleteResponse {
  ok?: boolean;
}

// schemas/ipc/sources/sources_list_request.json
export interface SourcesListRequest {}

// schemas/ipc/sources/sources_list_response.json
export interface SourcesListResponse {
  sources: SourcesListResponseSourceInfo[];
}
export interface SourcesListResponseSourceInfo {
  source_id: string;
  url: string;
  kind: string;
  connector_id?: string | null;
  company_id?: string | null;
  enabled?: boolean;
  interval_s?: number;
  extraction_order?: string[];
  tier?: string | null;
  tier_reason?: string | null;
}

// schemas/ipc/sources/sources_set_enabled_request.json
export interface SourcesSetEnabledRequest {
  source_id: string;
  enabled: boolean;
}

// schemas/ipc/sources/sources_set_enabled_response.json
export interface SourcesSetEnabledResponse {
  ok?: boolean;
}

// schemas/ipc/sources/sources_set_extraction_order_request.json
export interface SourcesSetExtractionOrderRequest {
  source_id: string;
  order?: string[];
}

// schemas/ipc/sources/sources_set_extraction_order_response.json
export interface SourcesSetExtractionOrderResponse {
  ok?: boolean;
}

// schemas/ipc/sources/sources_set_schedule_request.json
export interface SourcesSetScheduleRequest {
  source_id: string;
  interval_s: number;
}

// schemas/ipc/sources/sources_set_schedule_response.json
export interface SourcesSetScheduleResponse {
  ok?: boolean;
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
