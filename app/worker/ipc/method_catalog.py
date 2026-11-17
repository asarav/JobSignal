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
    CancelTaskParams,
    CrawlRunListResult,
    DiscoveryRunParams,
    EmptyParams,
    FetchUrlParams,
    FetchUrlResult,
    HealthResult,
    HelloParams,
    HelloResult,
    JobFilter,
    JobListResult,
    JobSearchParams,
    OkResult,
    PluginConfigureParams,
    PluginIdParams,
    PluginInfo,
    PluginListResult,
    PluginOptionalIdParams,
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


MILESTONE_METHODS: tuple[MethodSpec, ...] = (
    MethodSpec("system.hello", HelloParams, HelloResult),
    MethodSpec("system.health", EmptyParams, HealthResult),
    MethodSpec("system.shutdown", EmptyParams, OkResult),
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
    MethodSpec("crawl_runs.list", EmptyParams, CrawlRunListResult),
    MethodSpec("discovery.run", DiscoveryRunParams, OkResult),
)

MILESTONE_METHOD_NAMES: tuple[str, ...] = tuple(spec.method for spec in MILESTONE_METHODS)

# Method prefixes that MUST remain absent until their milestone lands. The
# negative test in CI asserts none appears in MILESTONE_METHOD_NAMES or Rust's
# generated allowlist.
FORBIDDEN_METHOD_PREFIXES: tuple[str, ...] = (
    "matching.",
    "applications.",
    "posting_intelligence.",
    "resume.",
    "keywords.",
    "browser.",
    "search_profiles.",
)


def result_models() -> tuple[type[BaseModel], ...]:
    seen: set[str] = set()
    models: list[type[BaseModel]] = []
    for spec in MILESTONE_METHODS:
        for model in (spec.params, spec.result):
            name = model.__name__
            if name not in seen:
                seen.add(name)
                models.append(model)
    # Nested list model used by PluginListResult.
    if PluginInfo.__name__ not in seen:
        models.append(PluginInfo)
    return models
