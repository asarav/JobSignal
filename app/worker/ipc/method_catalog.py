"""Canonical M0 IPC method catalogue.

This is the single source of truth for:
- the Python dispatcher registration (smoke-tested against ``main``),
- generated JSON Schemas / TypeScript types,
- the Rust method allowlist (via ``schemas/ipc/methods.json``).

Later milestones append methods here; M0 intentionally exposes no discovery,
jobs, browser, matching, resume, keyword, application, company or posting
intelligence method.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.application.dto import (
    AnalyticsParams,
    AnalyticsResult,
    ApplicationCreateParams,
    ApplicationEventParams,
    ApplicationInfo,
    ApplicationListParams,
    ApplicationListResult,
    ApplicationOutcomeParams,
    ApplicationStatusParams,
    AutoDiscoverResult,
    BackupExportParams,
    BackupExportResult,
    BackupHistoryParams,
    BackupHistoryResult,
    BackupImportParams,
    BackupImportResult,
    BrowserAutofillParams,
    BrowserCloseParams,
    BrowserFillReportResult,
    BrowserFormResult,
    BrowserHandoffParams,
    BrowserHandoffResult,
    BrowserInspectParams,
    BrowserOpenJobParams,
    BrowserPlanParams,
    BrowserPlanResult,
    BrowserSessionResult,
    CalibrationResult,
    CancelTaskParams,
    CompanyDetailParams,
    CompanyDetailResult,
    CompanyHistoryParams,
    CompanyHistoryResult,
    CompanyListResult,
    CompanyMergeHistoryParams,
    CompanyMergeHistoryResult,
    CompanyMergeParams,
    CompanyMergeResult,
    CrawlRunListParams,
    CrawlRunListResult,
    DiscoveryRunParams,
    DiscoveryRunResult,
    EmptyParams,
    EndpointVerifyResult,
    FetchUrlParams,
    FetchUrlResult,
    HealthResult,
    HelloParams,
    HelloResult,
    JobDetailResult,
    JobFilter,
    JobIdParams,
    JobListResult,
    JobMergeParams,
    JobSearchParams,
    JobsListSavedParams,
    JobUserStateParams,
    JobUserStateResult,
    KeywordsBackfillParams,
    KeywordsBackfillResult,
    KeywordsCoverageParams,
    KeywordsCoverageResult,
    KeywordsExtractParams,
    KeywordsExtractResult,
    KeywordsPrevalenceParams,
    KeywordsPrevalenceResult,
    KeywordsTrendsParams,
    KeywordsTrendsResult,
    MatchingApplyWeightsParams,
    MatchingApplyWeightsResult,
    MatchingResultsResult,
    MatchingRunParams,
    MatchingRunResult,
    MatchingWeightHistoryParams,
    MatchingWeightHistoryResult,
    MatchingWeightProposalResult,
    MatchingWeightProposalsParams,
    MatchingWeightsParams,
    MatchingWeightsResult,
    OkResult,
    PluginConfigureParams,
    PluginIdParams,
    PluginListResult,
    PluginOptionalIdParams,
    PostingIntelligenceCompanyParams,
    PostingIntelligenceCompanyResult,
    PostingIntelligenceConfigParams,
    PostingIntelligenceConfigResult,
    PostingIntelligenceEvaluateParams,
    PostingIntelligenceEvaluateResult,
    PostingIntelligenceHistoryResult,
    PostingIntelligenceSignalsResult,
    RankedJobListResult,
    RankedJobParams,
    RestoreSuppressedJobParams,
    ResumeImportParams,
    ResumeImportResult,
    ResumeOverridesParams,
    ResumeParseParams,
    ResumeParseResult,
    ResumeProfileParams,
    ResumeProfileResult,
    ResumeSetActiveParams,
    ResumeSkillStatusParams,
    ResumeVersionsResult,
    SearchProfileInfo,
    SearchProfileListResult,
    SearchProfileParams,
    SearchProfileSaveParams,
    SettingsPatch,
    SettingsResult,
    SleepTaskParams,
    SourceAddParams,
    SourceIdParams,
    SourceInfo,
    SourceListResult,
    SourceSetEnabledParams,
    SourceSetExtractionOrderParams,
    SourceSetScheduleParams,
    SuppressionListResult,
    SuppressionPolicyParams,
    SuppressionPolicyResult,
    TaskAcceptedResult,
    TaskFilter,
    TaskListResult,
)

PROTOCOL_VERSION = 1


class PluginCapabilitiesResult(BaseModel):
    capabilities: dict[str, list[str]]


class PluginConfigResult(BaseModel):
    config: dict[str, Any]


class EnvelopeSchema(BaseModel):
    """Wire envelope union, kept narrow for M0 consumers."""

    v: Literal[1] = 1
    type: Literal["request", "response", "event"]
    id: str | None = None
    method: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)
    ok: bool | None = None
    result: Any = None
    error: dict[str, Any] | None = None
    topic: str | None = None
    seq: int = 0
    ts: str = ""
    task_id: str | None = None
    data: Any = None


class WorkerReadyData(BaseModel):
    protocol_versions: list[int]
    app_version: str
    schema_version: int


class WorkerReadyEvent(BaseModel):
    v: Literal[1] = 1
    type: Literal["event"] = "event"
    topic: Literal["worker.ready"] = "worker.ready"
    seq: int = 0
    ts: str = ""
    data: WorkerReadyData


@dataclass(frozen=True, slots=True)
class MethodSpec:
    method: str
    params: type[BaseModel]
    result: type[BaseModel]
    long_running: bool = False
    #: How long the desktop shell waits for the response. One HTTP request is
    #: bounded by ``http.timeout_s``; a crawl of one board, a pass over every
    #: due source or a matching run over the whole library is not, and killing
    #: the wait while the worker keeps working produced a false ``task.timeout``
    #: for work that was still running to completion.
    timeout_s: float = 60.0


#: Ceiling for the two discovery entry points: a pass walks index slices and
#: crawls up to ``discovery.max_crawl_sources_per_run`` boards.
DISCOVERY_TIMEOUT_S = 900.0
#: Matching evaluates every job against the active profile.
MATCHING_TIMEOUT_S = 600.0


MILESTONE_METHODS: tuple[MethodSpec, ...] = (
    MethodSpec("system.hello", HelloParams, HelloResult),
    MethodSpec("system.health", EmptyParams, HealthResult),
    MethodSpec("system.shutdown", EmptyParams, OkResult),
    MethodSpec("system.export_backup", BackupExportParams, BackupExportResult),
    MethodSpec("system.import_backup", BackupImportParams, BackupImportResult),
    MethodSpec(
        "system.get_backup_history",
        BackupHistoryParams,
        BackupHistoryResult,
    ),
    MethodSpec("tasks.list", TaskFilter, TaskListResult),
    MethodSpec("tasks.cancel", CancelTaskParams, OkResult),
    MethodSpec("settings.get", EmptyParams, SettingsResult),
    MethodSpec("settings.update", SettingsPatch, SettingsResult),
    MethodSpec("plugins.list", EmptyParams, PluginListResult),
    MethodSpec("plugins.enable", PluginIdParams, OkResult),
    MethodSpec("plugins.disable", PluginIdParams, OkResult),
    MethodSpec("plugins.configure", PluginConfigureParams, PluginConfigResult),
    MethodSpec("plugins.get_capabilities", PluginOptionalIdParams, PluginCapabilitiesResult),
    MethodSpec("plugins.reset_circuit", PluginIdParams, OkResult),
    MethodSpec("diagnostics.fetch_url", FetchUrlParams, FetchUrlResult),
    MethodSpec("diagnostics.sleep", SleepTaskParams, TaskAcceptedResult, long_running=True),
    # M1: discovery + sources + jobs + crawl runs
    MethodSpec("sources.list", EmptyParams, SourceListResult),
    MethodSpec("sources.add", SourceAddParams, SourceInfo),
    MethodSpec("sources.set_enabled", SourceSetEnabledParams, OkResult),
    MethodSpec("sources.set_extraction_order", SourceSetExtractionOrderParams, OkResult),
    MethodSpec("sources.set_schedule", SourceSetScheduleParams, OkResult),
    MethodSpec("sources.delete", SourceIdParams, OkResult),
    MethodSpec("jobs.list", JobFilter, JobListResult),
    MethodSpec("jobs.search", JobSearchParams, JobListResult),
    MethodSpec("jobs.merge", JobMergeParams, OkResult),
    MethodSpec("jobs.unmerge", JobMergeParams, OkResult),
    MethodSpec("jobs.list_duplicates", EmptyParams, JobListResult),
    MethodSpec("jobs.list_seen", EmptyParams, JobListResult),
    MethodSpec("jobs.ranked", RankedJobParams, RankedJobListResult),
    MethodSpec("jobs.list_saved", JobsListSavedParams, JobListResult),
    MethodSpec("jobs.get_detail", JobIdParams, JobDetailResult),
    MethodSpec(
        "jobs.verify_now",
        JobIdParams,
        EndpointVerifyResult,
        long_running=True,
        timeout_s=MATCHING_TIMEOUT_S,
    ),
    # M5: resume + matching
    MethodSpec("resume.import", ResumeImportParams, ResumeImportResult),
    MethodSpec("resume.list_versions", ResumeProfileParams, ResumeVersionsResult),
    MethodSpec("resume.get_profile", ResumeProfileParams, ResumeProfileResult),
    MethodSpec("resume.set_active", ResumeSetActiveParams, OkResult),
    MethodSpec("resume.update_overrides", ResumeOverridesParams, OkResult),
    MethodSpec("resume.parse", ResumeParseParams, ResumeParseResult),
    MethodSpec("resume.set_skill_status", ResumeSkillStatusParams, OkResult),
    MethodSpec("matching.run", MatchingRunParams, MatchingRunResult,
               timeout_s=MATCHING_TIMEOUT_S),
    MethodSpec("matching.get_results", ResumeProfileParams, MatchingResultsResult),
    MethodSpec("matching.update_weights", MatchingWeightsParams, MatchingWeightsResult),
    MethodSpec(
        "matching.get_weight_proposals",
        MatchingWeightProposalsParams,
        MatchingWeightProposalResult,
    ),
    MethodSpec(
        "matching.apply_weight_proposal",
        MatchingApplyWeightsParams,
        MatchingApplyWeightsResult,
    ),
    MethodSpec(
        "matching.get_weight_history",
        MatchingWeightHistoryParams,
        MatchingWeightHistoryResult,
    ),
    MethodSpec("keywords.extract", KeywordsExtractParams, KeywordsExtractResult),
    MethodSpec("keywords.prevalence", KeywordsPrevalenceParams, KeywordsPrevalenceResult),
    MethodSpec("keywords.coverage", KeywordsCoverageParams, KeywordsCoverageResult),
    MethodSpec("keywords.trends", KeywordsTrendsParams, KeywordsTrendsResult),
    MethodSpec("keywords.backfill", KeywordsBackfillParams, KeywordsBackfillResult),
    MethodSpec("crawl_runs.list", CrawlRunListParams, CrawlRunListResult),
    MethodSpec("discovery.run", DiscoveryRunParams, DiscoveryRunResult,
               timeout_s=DISCOVERY_TIMEOUT_S),
    # M3: search profiles
    MethodSpec("search_profiles.list", EmptyParams, SearchProfileListResult),
    MethodSpec("search_profiles.save", SearchProfileSaveParams, SearchProfileInfo),
    MethodSpec("search_profiles.delete", SearchProfileParams, OkResult),
    # M4: auto-discovery
    MethodSpec(
        "discovery.auto_discover",
        EmptyParams,
        AutoDiscoverResult,
        timeout_s=DISCOVERY_TIMEOUT_S,
    ),
    # M7: applications, suppression and user job state
    MethodSpec("applications.create", ApplicationCreateParams, ApplicationInfo),
    MethodSpec("applications.update_status", ApplicationStatusParams, ApplicationInfo),
    MethodSpec("applications.add_event", ApplicationEventParams, OkResult),
    MethodSpec("applications.record_outcome", ApplicationOutcomeParams, OkResult),
    MethodSpec("applications.list", ApplicationListParams, ApplicationListResult),
    MethodSpec(
        "applications.get_company_history", CompanyHistoryParams, CompanyHistoryResult
    ),
    MethodSpec("applications.get_analytics", AnalyticsParams, AnalyticsResult),
    MethodSpec("applications.get_suppressions", EmptyParams, SuppressionListResult),
    MethodSpec(
        "applications.get_suppression_policy", EmptyParams, SuppressionPolicyResult
    ),
    MethodSpec(
        "applications.restore_suppressed_job", RestoreSuppressedJobParams, OkResult
    ),
    MethodSpec(
        "applications.update_suppression_policy",
        SuppressionPolicyParams,
        SuppressionPolicyResult,
    ),
    MethodSpec("jobs.set_user_state", JobUserStateParams, JobUserStateResult),
    # M9: company breadth is read-only; merging is a separate, audited action.
    MethodSpec("companies.list", EmptyParams, CompanyListResult),
    MethodSpec("companies.get_detail", CompanyDetailParams, CompanyDetailResult),
    MethodSpec("companies.merge", CompanyMergeParams, CompanyMergeResult),
    MethodSpec("companies.unmerge", CompanyDetailParams, CompanyMergeResult),
    MethodSpec(
        "companies.get_merge_history",
        CompanyMergeHistoryParams,
        CompanyMergeHistoryResult,
    ),
    # M7: headed browsing and reviewed autofill (never submits)
    MethodSpec("browser.open_job", BrowserOpenJobParams, BrowserSessionResult),
    MethodSpec("browser.inspect_form", BrowserInspectParams, BrowserFormResult),
    MethodSpec("browser.autofill_plan", BrowserPlanParams, BrowserPlanResult),
    MethodSpec("browser.autofill", BrowserAutofillParams, BrowserFillReportResult),
    # The only way to stop intercepting: the user takes the window over to
    # submit themselves (§13.4.5). It cannot submit anything itself.
    MethodSpec("browser.handoff", BrowserHandoffParams, BrowserHandoffResult),
    MethodSpec("browser.close_session", BrowserCloseParams, OkResult),
    # M8: posting intelligence reads append-only history and never
    # touches the posting itself. Evaluation is queued: a daily pass can
    # cover every active job.
    MethodSpec(
        "posting_intelligence.evaluate",
        PostingIntelligenceEvaluateParams,
        PostingIntelligenceEvaluateResult,
        timeout_s=DISCOVERY_TIMEOUT_S,
    ),
    MethodSpec(
        "posting_intelligence.get_signals",
        JobIdParams,
        PostingIntelligenceSignalsResult,
    ),
    MethodSpec(
        "posting_intelligence.get_risk_history",
        JobIdParams,
        PostingIntelligenceHistoryResult,
    ),
    MethodSpec(
        "posting_intelligence.get_company_assessment",
        PostingIntelligenceCompanyParams,
        PostingIntelligenceCompanyResult,
    ),
    MethodSpec(
        "posting_intelligence.configure_model",
        PostingIntelligenceConfigParams,
        PostingIntelligenceConfigResult,
    ),
    MethodSpec(
        "posting_intelligence.get_calibration",
        EmptyParams,
        CalibrationResult,
    ),
)

MILESTONE_METHOD_NAMES: tuple[str, ...] = tuple(spec.method for spec in MILESTONE_METHODS)

# Methods that must never exist. Autofill can fill; the user submits.
# The negative test in CI asserts none appears in MILESTONE_METHOD_NAMES or
# Rust's generated allowlist.
FORBIDDEN_METHOD_PREFIXES: tuple[str, ...] = (
    "browser.submit",
    "browser.click",
    "browser.press",
    "browser.evaluate",
    "browser.type",
    "applications.submit",
)
