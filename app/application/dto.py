"""Application DTOs used at the IPC boundary.

These are transport-facing, not domain entities: they only name fields the
worker accepts/returns. Domain models remain the source of truth for meaning.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyParams(DTO):
    pass


class HelloParams(DTO):
    protocol_version: int
    ui_version: str


class HelloResult(DTO):
    protocol_version: int
    app_version: str
    schema_version: int
    plugin_api_version: str
    capabilities: list[str]


class HealthResult(DTO):
    status: str
    app_version: str
    schema_version: int
    protocol_version: int
    data_dir: str
    disk_usage_bytes: int
    plugin_health: list[dict[str, Any]] = Field(default_factory=list)
    queue_depth: int = 0


class SettingsPatch(DTO):
    patch: dict[str, Any]


class SettingsResult(DTO):
    config: dict[str, Any]
    overrides: dict[str, Any]
    schema_version: int


class TaskFilter(DTO):
    status: str | None = None
    limit: int = 50


class CancelTaskParams(DTO):
    task_id: str


class TaskSummary(DTO):
    id: str
    type: str
    status: str
    priority: int
    params: dict[str, Any] = Field(default_factory=dict)
    progress: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    parent_task_id: str | None = None


class TaskListResult(DTO):
    tasks: list[TaskSummary]


class PluginIdParams(DTO):
    plugin_id: str


class PluginOptionalIdParams(DTO):
    plugin_id: str | None = None


class PluginConfigureParams(DTO):
    plugin_id: str
    config: dict[str, Any] = Field(default_factory=dict)


class PluginInfo(DTO):
    plugin_id: str
    kind: str
    version: str
    api_version: str
    enabled: bool
    health: dict[str, Any]
    capabilities: list[str]
    network_hosts: list[str]
    description: str


class PluginListResult(DTO):
    plugins: list[PluginInfo]


class FetchUrlParams(DTO):
    url: str


class FetchUrlResult(DTO):
    status: int
    url: str
    elapsed_ms: int
    bytes: int


class SleepTaskParams(DTO):
    seconds: float = 0.05
    steps: int = 2


class TaskAcceptedResult(DTO):
    task_id: str


class SearchProfileInfo(DTO):
    profile_id: str
    name: str
    candidate_profile_id: str = ""
    scoring_config_id: str | None = None
    ranking_policy: str = "deprioritize"
    hard_filters: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)


class SearchProfileListResult(DTO):
    profiles: list[SearchProfileInfo]


class SearchProfileParams(DTO):
    profile_id: str


class SearchProfileSaveParams(DTO):
    profile_id: str | None = None
    name: str
    candidate_profile_id: str = ""
    scoring_config_id: str | None = None
    ranking_policy: str = "deprioritize"
    hard_filters: dict[str, Any] = Field(default_factory=dict)
    preferences: dict[str, Any] = Field(default_factory=dict)


class OkResult(DTO):
    ok: bool = True


# --- M7: applications, suppression, user job state -------------------------


class ApplicationCreateParams(DTO):
    job_id: str
    status: str = "applied"
    applied_at: str | None = None
    notes: str = ""
    resume_version_id: str | None = None


class ApplicationStatusParams(DTO):
    application_id: str
    status: str
    applied_at: str | None = None
    note: str = ""


class ApplicationEventParams(DTO):
    application_id: str
    kind: str = "note"
    note: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class AnalyticsParams(DTO):
    days: int = Field(default=365, ge=1, le=3650)
    profile_id: str = "default"


class AnalyticsResult(DTO):
    generated_at: str
    window_days: int
    applications: int
    responded: int
    overall: dict[str, Any]
    min_sample: int
    groups: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class CalibrationResult(DTO):
    sample_size: int
    min_sample: int
    observed_closed: int
    buckets: list[dict[str, Any]] = Field(default_factory=list)
    proposal: dict[str, Any] | None = None
    note: str = ""


class ApplicationOutcomeParams(DTO):
    application_id: str
    kind: str
    occurred_at: str | None = None
    note: str = ""


class ApplicationListParams(DTO):
    view: Literal[
        "all", "active", "interviewing", "offers", "closed", "follow_up"
    ] = "all"
    query: str | None = None
    sort: Literal["newest", "oldest", "updated"] = "newest"
    limit: int = Field(default=25, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class ApplicationSummary(DTO):
    total: int = 0
    applied: int = 0
    active: int = 0
    interviewing: int = 0
    offers: int = 0
    closed: int = 0
    follow_up: int = 0
    applied_last_7_days: int = 0
    #: Applications with any confirmed response outcome, all time.
    responded: int = 0
    responded_last_7_days: int = 0


class ApplicationInfo(DTO):
    application_id: str
    company_id: str
    company_name: str = ""
    job_id: str
    job_title: str
    job_title_family: str | None = None
    status: str
    applied_at: str | None = None
    resume_version_id: str | None = None
    notes: str = ""
    created_at: str | None = None
    updated_at: str | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    outcomes: list[dict[str, Any]] = Field(default_factory=list)
    suppressions: list[dict[str, Any]] = Field(default_factory=list)


class ApplicationListResult(DTO):
    applications: list[ApplicationInfo]
    total: int = 0
    summary: ApplicationSummary = Field(default_factory=ApplicationSummary)


class CompanyHistoryParams(DTO):
    company_id: str


class CompanyHistoryResult(DTO):
    company_id: str
    applications: list[ApplicationInfo]
    total: int = 0
    total_applied: int = 0
    last_applied_at: str | None = None


class SuppressionInfo(DTO):
    suppression_id: str
    job_id: str
    application_id: str
    job_title: str | None = None
    company_id: str | None = None
    state: str
    reason: str
    explanation: str
    similarity: float = 0.0
    expires_at: str | None = None
    created_at: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)


class SuppressionListResult(DTO):
    suppressions: list[SuppressionInfo]
    total: int = 0
    #: The effective policy, so the Suppressed view can show the current rules.
    policy: dict[str, Any] = Field(default_factory=dict)


class RestoreSuppressedJobParams(DTO):
    job_id: str
    application_id: str | None = None


class SuppressionPolicyParams(DTO):
    policy: dict[str, Any] = Field(default_factory=dict)


class SuppressionPolicyResult(DTO):
    policy: dict[str, Any]


class JobUserStateParams(DTO):
    job_id: str
    state: Literal["new", "saved", "dismissed", "hidden"] = "saved"
    note: str | None = None


class JobUserStateResult(DTO):
    job_id: str
    state: str
    hidden: bool = False


class BrowserOpenJobParams(DTO):
    job_id: str


class BrowserSessionResult(DTO):
    session_id: str
    url: str
    job_id: str = ""


class BrowserInspectParams(DTO):
    session_id: str


class BrowserFormResult(DTO):
    session_id: str
    url: str
    step_index: int = 0
    step_count: int = 1
    is_final_step: bool | None = None
    fields: list[dict[str, Any]] = Field(default_factory=list)
    controls: list[dict[str, Any]] = Field(default_factory=list)


class BrowserPlanParams(DTO):
    session_id: str


class BrowserPlanResult(DTO):
    plan_id: str
    session_id: str
    url: str = ""
    step_index: int = 0
    items: list[dict[str, Any]] = Field(default_factory=list)


class BrowserAutofillParams(DTO):
    session_id: str
    plan_id: str


class BrowserFillReportResult(DTO):
    plan_id: str
    filled: list[dict[str, Any]] = Field(default_factory=list)
    skipped: list[dict[str, Any]] = Field(default_factory=list)
    blocked: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    #: Always false: no code path can submit (spec §13.5).
    submitted: bool = False


class BrowserCloseParams(DTO):
    session_id: str


class BrowserHandoffParams(DTO):
    session_id: str


class BrowserHandoffResult(DTO):
    session_id: str
    #: False once the guard is off, i.e. the user is submitting from now on.
    interceptor_active: bool = False
    #: Always false: handing over is the user's action, not the app's (§13.5).
    submitted: bool = False


class SourceInfo(DTO):
    source_id: str
    url: str
    kind: str
    connector_id: str | None = None
    company_id: str | None = None
    enabled: bool = True
    interval_s: float = 0.0
    extraction_order: list[str] = Field(default_factory=list)
    tier: str | None = None
    tier_reason: str | None = None


class SourceListResult(DTO):
    sources: list[SourceInfo]
    #: Every enabled board, including the rows omitted from ``sources`` when the
    #: full seed has been imported.  The UI shows the bounded page, not 58k rows.
    total: int = 0
    truncated: bool = False


class SourceAddParams(DTO):
    url: str
    connector_id: str | None = None
    company_id: str | None = None
    interval_s: float = Field(default=3600.0, ge=0, le=86400)


class SourceIdParams(DTO):
    source_id: str


class SourceSetEnabledParams(DTO):
    source_id: str
    enabled: bool


class SourceSetExtractionOrderParams(DTO):
    source_id: str
    order: list[str] = Field(default_factory=list)


class SourceSetScheduleParams(DTO):
    source_id: str
    interval_s: float = Field(ge=0, le=86400)


class JobSummary(DTO):
    job_id: str
    title: str
    company_id: str | None = None
    canonical_url: str | None = None
    posting_status: str = "active"
    source_id: str | None = None
    provenance: list[str] = Field(default_factory=list)
    via_index: bool = False
    #: Why a row appears in the Seen/Duplicates view (``duplicate``,
    #: ``posting_removed`` …); ``None`` in ordinary result lists.
    reason: str | None = None
    related_job_ids: list[str] = Field(default_factory=list)
    company_name: str = ""
    location_text: str = ""
    workplace_type: str = "unknown"
    salary_min: int | None = None
    salary_max: int | None = None
    salary_currency: str | None = None
    posted_at: str | None = None
    first_seen_at: str | None = None
    #: Heuristic fit (satisfaction) and credibility (success evidence) are
    #: separate, and neither is a probability of an interview.
    fit_score: float | None = None
    risk: float | None = None
    risk_confidence: float | None = None
    risk_class: str | None = None
    risk_label: str | None = None
    credibility: float | None = None
    saved: bool = False
    #: True when a saved job already has an application (the apply queue hides it).
    tracked: bool = False
    rank_score: float | None = None
    rank_reasons: list[str] = Field(default_factory=list)
    hidden_by_policy: bool = False
    #: Section-aware requirements: required and preferred are different.
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    missing_required_skills: list[str] = Field(default_factory=list)
    required_skill_coverage: float | None = None
    #: Parsed country/region eligibility and its human-readable restriction.
    location_scope: dict[str, Any] = Field(default_factory=dict)


class CompanyInfo(DTO):
    company_id: str
    name: str
    domain: str | None = None
    job_count: int = 0
    active_job_count: int = 0
    application_count: int = 0
    last_seen_at: str | None = None
    merged_ids: list[str] = Field(default_factory=list)
    merged_company_count: int = 0


class CompanyListResult(DTO):
    companies: list[CompanyInfo] = Field(default_factory=list)
    total: int = 0


class CompanyDetailParams(DTO):
    company_id: str


class CompanyMergeParams(DTO):
    company_id: str
    into_id: str
    reason: str = ""


class CompanyMergeResult(DTO):
    company_id: str
    survivor_id: str
    action: str
    changed: bool = False
    record_id: str | None = None
    previous_survivor_id: str | None = None


class CompanyMergeHistoryParams(DTO):
    company_id: str | None = None
    limit: int = Field(default=200, ge=1, le=1000)


class CompanyMergeHistoryResult(DTO):
    company_id: str | None = None
    records: list[dict[str, Any]] = Field(default_factory=list)
    currently_merged: dict[str, str] = Field(default_factory=dict)
    total: int = 0


class CompanyTrendParams(DTO):
    company_id: str
    days: int = Field(default=90, ge=1, le=365)


class CompanyTrendPoint(DTO):
    day: str
    computed_at: str
    n_unique_postings: int = 0
    #: Rates are withheld below the adequate sample tier: a small sample is a
    #: count, never a trend.
    repost_ratio: float | None = None
    inactive_ratio: float | None = None
    sample_tier: str = "insufficient"


class CompanyTrendResult(DTO):
    company_id: str
    name: str = ""
    requested_id: str | None = None
    days: int = 90
    since: str = ""
    points: list[CompanyTrendPoint] = Field(default_factory=list)
    n_points: int = 0
    note: str = ""


class CompanyDetailResult(DTO):
    company_id: str
    name: str
    requested_id: str | None = None
    merged_ids: list[str] = Field(default_factory=list)
    domain: str | None = None
    jobs: list[JobSummary] = Field(default_factory=list)
    job_count: int = 0
    active_job_count: int = 0
    repost_count: int = 0
    summary: dict[str, Any] | None = None
    applications: list[dict[str, Any]] = Field(default_factory=list)
    recent_window_days: int = 90
    window_start: str | None = None


class JobListResult(DTO):
    jobs: list[JobSummary]
    total: int = 0


class JobSearchParams(DTO):
    query: str
    limit: int = 50


class RankedJobParams(DTO):
    search_profile_id: str | None = None
    ranking_policy: str | None = None
    query: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    sort: Literal["rank", "fit", "freshness", "credibility"] = "rank"
    include_suppressed: bool = False
    include_hidden: bool = False
    #: Reveal roles outside the candidate's stated area.  The default withholds
    #: a posting whose known location conflicts with the profile and relocation
    #: is off; unknown locations are always shown.
    include_out_of_area: bool = False
    #: Reveal roles whose confidently extracted required skills are mostly
    #: missing from the profile.  Jobs with no extracted requirements stay.
    include_missing_skills: bool = False
    #: Compute missing fit for the ranking window (bounded).
    ensure_fit: bool = False
    #: Optional display filters applied after fit/risk are resolved.
    min_fit: float | None = Field(default=None, ge=0, le=1)
    risk_classes: list[str] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
    #: How many recent matches are considered before ranking.
    window: int = Field(default=500, ge=1, le=2000)
    #: ISO timestamp of the previous visit, for the whole-corpus "new" count.
    seen_since: str | None = None


class RankedJobListResult(DTO):
    jobs: list[JobSummary] = Field(default_factory=list)
    total: int = 0
    profile_id: str = "default"
    policy: str = "deprioritize"
    window: int = 0
    window_truncated: bool = False
    hidden_by_policy: int = 0
    #: Rows withheld because their known location is outside the profile's area.
    outside_area: int = 0
    #: Rows withheld because required-skill coverage was below the gate.
    hidden_missing_skills: int = 0
    #: Whole-corpus counts, not the paged/ranked slice (dashboard headline).
    needs_attention: int = 0
    new_since: int | None = None


class JobsListSavedParams(DTO):
    view: Literal["ready", "all"] = "ready"
    query: str | None = None
    sort: Literal["rank", "newest", "company"] = "rank"
    limit: int = Field(default=25, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class JobFilter(DTO):
    source_id: str | None = None
    company_id: str | None = None
    query: str | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    limit: int = 50
    offset: int = 0


class JobMergeParams(DTO):
    job_id: str
    related_job_id: str


class JobIdParams(DTO):
    job_id: str


class JobDetailResult(DTO):
    job: dict[str, Any]
    snapshots: list[dict[str, Any]] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    reposts: list[dict[str, Any]] = Field(default_factory=list)

class ResumeImportResult(DTO):
    profile_id: str
    version_id: str
    skills: list[str] = Field(default_factory=list)
    sections: list[str] = Field(default_factory=list)
    contact: dict[str, str] = Field(default_factory=dict)
    years_experience: float | None = None
    experience: int = 0
    education: int = 0
    certifications: int = 0
    projects: int = 0

class ResumeVersionsResult(DTO):
    versions: list[dict[str, Any]] = Field(default_factory=list)

class ResumeProfileResult(DTO):
    profile: dict[str, Any] | None = None

class MatchingRunResult(DTO):
    results: list[dict[str, Any]] = Field(default_factory=list)
    #: What the run considered, so "nothing happened" is never the only answer.
    available_jobs: int = 0
    considered: int = 0
    scored: int = 0
    reused: int = 0
    truncated: bool = False

class MatchingResultsResult(DTO):
    results: list[dict[str, Any]] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)
    #: Counts the candidate can reveal with the opt-in flags.
    hidden_out_of_area: int = 0
    hidden_missing_skills: int = 0


class MatchingResultsParams(DTO):
    profile_id: str = "default"
    limit: int = Field(default=50, ge=1, le=500)
    include_out_of_area: bool = False
    include_missing_skills: bool = False


class MatchingWeightsResult(DTO):
    ok: bool = True
    weights: dict[str, float] = Field(default_factory=dict)


class MatchingWeightProposalsParams(DTO):
    profile_id: str = "default"
    days: int = Field(default=365, ge=1, le=3650)


class MatchingWeightProposalResult(DTO):
    profile_id: str
    generated_at: str
    window_days: int = 365
    applications: int = 0
    responded: int = 0
    not_responded: int = 0
    min_sample: int = 20
    min_per_side: int = 5
    min_gap: float = 0.05
    weights: dict[str, float] = Field(default_factory=dict)
    associations: list[dict[str, Any]] = Field(default_factory=list)
    insufficient_evidence: bool = True
    proposal: dict[str, Any] | None = None
    latest_version: dict[str, Any] | None = None
    note: str = ""


class MatchingApplyWeightsParams(DTO):
    profile_id: str = "default"
    weights: dict[str, Any] = Field(default_factory=dict)
    evidence: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class MatchingApplyWeightsResult(DTO):
    ok: bool = True
    profile_id: str
    version_id: str
    weights: dict[str, float] = Field(default_factory=dict)
    previous_weights: dict[str, float] | None = None
    source: str
    created_at: str


class MatchingWeightHistoryParams(DTO):
    profile_id: str = "default"
    limit: int = Field(default=50, ge=1, le=200)


class MatchingWeightHistoryResult(DTO):
    profile_id: str
    weights: dict[str, float] = Field(default_factory=dict)
    versions: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class ResumeSkillStatusParams(DTO):
    profile_id: str = "default"
    keyword_id: str
    status: Literal[
        "extensive_experience",
        "have_experience",
        "learning",
        "no_experience",
        "not_relevant",
    ]


class ResumeParseParams(DTO):
    resume_version_id: str


class ResumeParseResult(DTO):
    version_id: str
    text: str
    sections: dict[str, str] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)
    years_experience: float | None = None


class BackupExportParams(DTO):
    path: str | None = None


class BackupExportResult(DTO):
    ok: bool = True
    path: str
    checksum: str
    counts: dict[str, int] = Field(default_factory=dict)
    schema_version: int = 0
    created_at: str


class BackupImportParams(DTO):
    path: str
    confirm: bool = False


class BackupImportResult(DTO):
    ok: bool = True
    verified: bool = False
    path: str
    checksum: str
    counts: dict[str, int] = Field(default_factory=dict)
    schema_version: int = 0
    imported_at: str


class BackupHistoryParams(DTO):
    limit: int = Field(default=20, ge=1, le=200)


class BackupHistoryResult(DTO):
    runs: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class KeywordsExtractParams(DTO):
    text: str


class KeywordsExtractResult(DTO):
    keywords: list[str] = Field(default_factory=list)


class KeywordsPrevalenceParams(DTO):
    company_id: str | None = None
    min_sample: int = Field(default=10, ge=1, le=1000)
    limit: int = Field(default=50, ge=1, le=200)


class KeywordsPrevalenceResult(DTO):
    scope: str
    company_id: str | None = None
    n_jobs: int = 0
    min_sample: int = 10
    small_sample: bool = True
    keywords: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class KeywordsTrendsParams(DTO):
    company_id: str | None = None
    bucket: str = "month"
    periods: int = Field(default=12, ge=1, le=60)
    min_sample: int = Field(default=10, ge=1, le=1000)
    limit: int = Field(default=8, ge=1, le=50)
    keyword_ids: list[str] = Field(default_factory=list)


class KeywordsTrendsResult(DTO):
    scope: str
    company_id: str | None = None
    bucket: str = "month"
    min_sample: int = 10
    keywords: list[dict[str, Any]] = Field(default_factory=list)
    periods: list[dict[str, Any]] = Field(default_factory=list)
    n_jobs: int = 0
    note: str = ""


class KeywordsCoverageParams(DTO):
    company_id: str | None = None
    profile_id: str = "default"
    min_sample: int = Field(default=10, ge=1, le=1000)
    limit: int = Field(default=50, ge=1, le=200)


class KeywordsCoverageResult(DTO):
    scope: str
    company_id: str | None = None
    profile_id: str = "default"
    n_jobs: int = 0
    min_sample: int = 10
    small_sample: bool = True
    candidate_skills: int = 0
    coverage_ratio: float | None = None
    covered: list[dict[str, Any]] = Field(default_factory=list)
    missing: list[dict[str, Any]] = Field(default_factory=list)
    #: Required-keyword prevalence, so a nice-to-have is never shown as a must.
    required_keywords: list[dict[str, Any]] = Field(default_factory=list)
    missing_required: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class KeywordsBackfillParams(DTO):
    limit: int = Field(default=100, ge=1, le=1000)


class KeywordsBackfillResult(DTO):
    processed: int = 0
    remaining: int = 0

class ResumeImportParams(DTO):
    file_path: str
    label: str = "Imported"

class ResumeProfileParams(DTO):
    profile_id: str = "default"

class ResumeSetActiveParams(DTO):
    profile_id: str = "default"
    version_id: str

class ResumeOverridesParams(DTO):
    profile_id: str = "default"
    overrides: dict[str, Any] = Field(default_factory=dict)

class MatchingRunParams(DTO):
    profile_id: str = "default"
    job_ids: list[str] = Field(default_factory=list)

class MatchingWeightsParams(DTO):
    profile_id: str = "default"
    weights: dict[str, Any] = Field(default_factory=dict)



class CrawlRunSummary(DTO):
    run_id: str
    source_id: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class DiscoveryRunParams(DTO):
    source_id: str


class DiscoveryRunResult(DTO):
    discovered: int = 0
    new: int = 0
    updated: int = 0
    skipped: int = 0
    removed: int = 0
    errors: int = 0
    gone: int = 0
    refs_total: int | None = None
    truncated: bool = False
    errors_by_code: dict[str, int] = Field(default_factory=dict)
    error_samples: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None


class CrawlRunListParams(DTO):
    source_id: str | None = None
    limit: int = Field(default=50, ge=1, le=500)


class CrawlRunListResult(DTO):
    runs: list[CrawlRunSummary]


class AutoDiscoverResult(DTO):
    discovered: int = 0
    registered: int = 0
    crawled: int = 0
    failed: int = 0
    postings_harvested: int = 0
    postings_verified: int = 0
    tiers_changed: int = 0
    #: Candidates probed for liveness; live/dead/empty are its outcomes.
    probed: int = 0
    live: int = 0
    dead: int = 0
    empty: int = 0
    #: Candidates carried into the next pass instead of being dropped.
    queued: int = 0


class DiscoveryStatusResult(DTO):
    """What the background discovery loop is doing, without running a pass."""

    running: bool = False
    interval_minutes: int = 60
    last_pass_at: str | None = None
    next_pass_at: str | None = None
    last_summary: dict[str, Any] = Field(default_factory=dict)
    sources: int = 0
    enabled_sources: int = 0
    last_crawl_at: str | None = None
    phase: Literal["seeding", "first_run", "steady", "idle"] = "idle"
    initial_total: int = 0
    initial_done: int = 0
    initial_pending: int = 0
    active_crawls: int = 0
    queued_crawls: int = 0
    postings_found: int = 0
    last_error: str | None = None
    note: str = ""


def task_summary(task: Any) -> TaskSummary:
    def iso(value: Any) -> str | None:
        return value.isoformat() if value is not None else None

    return TaskSummary(
        id=task.id,
        type=task.type,
        status=task.status.value,
        priority=task.priority,
        params=dict(task.params),
        progress=dict(task.progress),
        error=dict(task.error) if task.error else None,
        created_at=iso(task.created_at),
        started_at=iso(task.started_at),
        finished_at=iso(task.finished_at),
        parent_task_id=task.parent_task_id,
    )


# --- M8: posting intelligence ------------------------------------------------


class PostingIntelligenceEvaluateParams(DTO):
    job_ids: list[str] = Field(default_factory=list)
    scope: Literal["active", "all"] = "active"
    #: Cap for a scope pass; 0 means every matching job.
    limit: int = 0
    refresh_endpoints: bool = False
    #: Re-assess even when the inputs have not changed (a model change or an
    #: explicit "re-check" from the UI). Ordinary runs reuse identical inputs.
    force: bool = False


class PostingIntelligenceEvaluateResult(DTO):
    task_id: str | None = None
    evaluated: int = 0
    skipped: int = 0
    job_ids: list[str] = Field(default_factory=list)


class PostingIntelligenceSignalsResult(DTO):
    job_id: str
    assessment: dict[str, Any] | None = None
    signals: list[dict[str, Any]] = Field(default_factory=list)


class PostingIntelligenceHistoryResult(DTO):
    job_id: str
    assessments: list[dict[str, Any]] = Field(default_factory=list)
    changes: list[dict[str, Any]] = Field(default_factory=list)


class PostingIntelligenceCompanyParams(DTO):
    company_id: str


class PostingIntelligenceCompanyResult(DTO):
    company_id: str
    summary: dict[str, Any] | None = None


class PostingIntelligenceConfigParams(DTO):
    config: dict[str, Any] = Field(default_factory=dict)


class EndpointVerifyResult(DTO):
    task_id: str | None = None
    checked: int = 0
    states: dict[str, int] = Field(default_factory=dict)
    job_ids: list[str] = Field(default_factory=list)


class PostingIntelligenceConfigResult(DTO):
    model_version_id: str
    model_id: str
    model_version: str
    config_hash: str
    config: dict[str, Any] = Field(default_factory=dict)
