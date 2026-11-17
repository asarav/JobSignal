"""Application DTOs used at the IPC boundary.

These are transport-facing, not domain entities: they only name fields the
worker accepts/returns. Domain models remain the source of truth for meaning.
"""

from __future__ import annotations

from typing import Any

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


class SourceInfo(DTO):
    source_id: str
    url: str
    kind: str
    connector_id: str | None = None
    company_id: str | None = None
    enabled: bool = True
    interval_s: float = 0.0
    extraction_order: list[str] = Field(default_factory=list)


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


class CrawlRunSummary(DTO):
    run_id: str
    source_id: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    stats: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class CrawlRunListResult(DTO):
    runs: list[CrawlRunSummary]


class DiscoveryRunParams(DTO):
    source_id: str


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
