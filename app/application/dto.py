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


class ApplicationOutcomeParams(DTO):
    application_id: str
    kind: str
    occurred_at: str | None = None
    note: str = ""


class ApplicationInfo(DTO):
    application_id: str
    company_id: str
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


class JobListResult(DTO):
    jobs: list[JobSummary]
    total: int = 0


class JobSearchParams(DTO):
    query: str
    limit: int = 50


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

class ResumeVersionsResult(DTO):
    versions: list[dict[str, Any]] = Field(default_factory=list)

class ResumeProfileResult(DTO):
    profile: dict[str, Any] | None = None

class MatchingRunResult(DTO):
    results: list[dict[str, Any]] = Field(default_factory=list)

class MatchingResultsResult(DTO):
    results: list[dict[str, Any]] = Field(default_factory=list)
    weights: dict[str, float] = Field(default_factory=dict)


class MatchingWeightsResult(DTO):
    ok: bool = True
    weights: dict[str, float] = Field(default_factory=dict)


class ResumeSkillStatusParams(DTO):
    profile_id: str = "default"
    keyword_id: str
    status: Literal["have_experience", "learning", "no_experience", "not_relevant"]


class ResumeParseParams(DTO):
    resume_version_id: str


class ResumeParseResult(DTO):
    version_id: str
    text: str
    sections: dict[str, str] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)
    years_experience: float | None = None


class KeywordsExtractParams(DTO):
    text: str


class KeywordsExtractResult(DTO):
    keywords: list[str] = Field(default_factory=list)

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
