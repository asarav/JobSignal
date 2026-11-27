"""Worker entrypoint: NDJSON IPC loop over stdin/stdout.

Run as: ``python -m app.worker.main --data-dir <path>``.
stderr carries structured JSON logs; stdout is reserved for protocol v1
envelopes only.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from app.application.auto_discover import (
    AUTO_DISCOVER_INTERVAL_S,
    AUTO_DISCOVER_POLL_S,
    AutoDiscoverUseCase,
    effective_crawl_interval_s,
)
from app.application.commands.companies import CompaniesUseCase
from app.application.commands.diagnostics import FetchUrlUseCase
from app.application.commands.discovery import DiscoveryUseCase
from app.application.commands.hello import HealthUseCase, HelloUseCase
from app.application.commands.jobs import JobsUseCase
from app.application.commands.plugins import PluginsUseCase
from app.application.commands.search_profiles import SearchProfilesUseCase
from app.application.commands.settings import SettingsUseCase
from app.application.commands.sources import SourcesUseCase
from app.application.commands.tasks import TasksUseCase
from app.application.config import (
    SETTINGS_KEY,
    AppConfig,
    ConfigState,
    discovery_settings,
)
from app.application.dto import DiscoveryRunParams
from app.application.orchestration.scheduler import CrawlScheduler
from app.application.sources import SourceRegistry
from app.domain.models.common import PortError
from app.domain.models.source import SourceConfig
from app.infrastructure.clock import SystemClock
from app.infrastructure.http import PolicyHttpClient
from app.infrastructure.http.policy_client import RateLimiter
from app.infrastructure.ids import UlidGenerator
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory
from app.plugins.registry import PluginRegistry
from app.worker.composition_root import build_container
from app.worker.ipc.dispatcher import Dispatcher, validate_request
from app.worker.ipc.envelope import PROTOCOL_VERSION, error_envelope, event_envelope, ok_envelope
from app.worker.ipc.events import QueuedEventPublisher, event_writer
from app.worker.ipc.handlers.companies import CompaniesHandlers
from app.worker.ipc.handlers.diagnostics import DiagnosticsHandlers
from app.worker.ipc.handlers.discovery import DiscoveryHandlers
from app.worker.ipc.handlers.jobs import JobsHandlers
from app.worker.ipc.handlers.plugins import PluginsHandlers
from app.worker.ipc.handlers.search_profiles import SearchProfilesHandlers
from app.worker.ipc.handlers.settings import SettingsHandlers
from app.worker.ipc.handlers.sources import SourcesHandlers
from app.worker.ipc.handlers.system import SystemHandlers
from app.worker.ipc.handlers.tasks import TasksHandlers
from app.worker.ipc.transport import NdjsonTransport

MAX_CONCURRENT_REQUESTS = 8

#: Per-source scheduler tasks the worker keeps alive.  The full seed can hold
#: tens of thousands of boards; passes cover anything beyond this cap, so the
#: registry cannot turn into tens of thousands of sleeping asyncio tasks.
MAX_SCHEDULED_SOURCES = 2000
#: How often due suppressions are lapsed in the background (spec §8.2: daily;
#: the query is indexed and cheap, so a shorter tick only makes expiry prompt).
MAINTENANCE_POLL_S = 900.0
#: Posting-intelligence maintenance runs on a slow, bounded cycle: a
#: daily verdict pass for active jobs and a budgeted endpoint sweep.
INTELLIGENCE_STARTUP_DELAY_S = 600.0
INTELLIGENCE_PASS_INTERVAL_S = 21_600.0

#: IPC methods that can change which sources exist or when they run.  Listing
#: sources must not reschedule the world, and neither must an extraction-order
#: change; every pass over the scheduler cancels and recreates tasks.
SCHEDULER_RESYNC_METHODS = frozenset(
    {"sources.add", "sources.set_enabled", "sources.set_schedule", "sources.delete"}
)

#: Methods that change the candidate signals a tier is computed from.  Tiering
#: is otherwise recomputed inside an auto-discovery pass; a resume or search
#: profile edit must not wait for the next pass to narrow (or widen) cadence.
TIER_AFFECTING_METHODS = frozenset(
    {
        "resume.import",
        "resume.set_active",
        "resume.update_overrides",
        "resume.set_skill_status",
        "search_profiles.save",
        "search_profiles.delete",
    }
)


def _auto_discover_interval_s(config: Any) -> float:
    """Pause between automatic passes (minutes preferred, hours for compat)."""
    settings = discovery_settings(config)
    minutes = settings.get("auto_discover_interval_minutes")
    if (
        isinstance(minutes, (int, float))
        and not isinstance(minutes, bool)
        and minutes > 0
    ):
        return float(minutes) * 60.0
    hours = settings.get("auto_discover_interval_hours")
    if isinstance(hours, (int, float)) and not isinstance(hours, bool) and hours > 0:
        return float(hours) * 3600.0
    return AUTO_DISCOVER_INTERVAL_S


def _response_succeeded(response: dict[str, Any]) -> bool:
    """Whether an IPC response carries a result rather than an error."""
    return "error" not in response


def _scheduler_affected(response: dict[str, Any]) -> bool:
    """Whether a pass changed sources or tiers and the scheduler must resync."""
    result = response.get("result")
    if not isinstance(result, dict):
        return False
    return any(
        isinstance(result.get(key), int) and result[key] > 0
        for key in ("registered", "tiers_changed")
    )


async def _load_config(uow_factory: SqliteUnitOfWorkFactory, data_dir: Path) -> AppConfig:
    base = AppConfig().with_data_dir(str(data_dir))
    async with uow_factory() as uow:
        stored = await uow.settings.get(SETTINGS_KEY) or {}
    try:
        return base.apply_patch(stored)
    except Exception as exc:  # noqa: BLE001 - invalid user settings block startup loudly
        raise SystemExit(f"stored settings are invalid: {exc}") from exc


def _build_dispatcher(
    container: Any, wiring: DiscoveryWiring | None = None
) -> Dispatcher:
    from app.application.dto import (
        AnalyticsParams,
        ApplicationCreateParams,
        ApplicationEventParams,
        ApplicationListParams,
        ApplicationOutcomeParams,
        ApplicationStatusParams,
        BackupExportParams,
        BackupHistoryParams,
        BackupImportParams,
        BrowserAutofillParams,
        BrowserCloseParams,
        BrowserHandoffParams,
        BrowserInspectParams,
        BrowserOpenJobParams,
        BrowserPlanParams,
        CancelTaskParams,
        CompanyDetailParams,
        CompanyHistoryParams,
        CompanyMergeHistoryParams,
        CompanyMergeParams,
        CompanyTrendParams,
        CrawlRunListParams,
        DiscoveryRunParams,
        EmptyParams,
        FetchUrlParams,
        HelloParams,
        JobFilter,
        JobIdParams,
        JobMergeParams,
        JobSearchParams,
        JobsListSavedParams,
        JobUserStateParams,
        KeywordsBackfillParams,
        KeywordsCoverageParams,
        KeywordsExtractParams,
        KeywordsPrevalenceParams,
        KeywordsTrendsParams,
        MatchingApplyWeightsParams,
        MatchingRunParams,
        MatchingWeightHistoryParams,
        MatchingWeightProposalsParams,
        MatchingWeightsParams,
        PluginConfigureParams,
        PluginIdParams,
        PluginOptionalIdParams,
        PostingIntelligenceCompanyParams,
        PostingIntelligenceConfigParams,
        PostingIntelligenceEvaluateParams,
        RankedJobParams,
        RestoreSuppressedJobParams,
        ResumeImportParams,
        ResumeOverridesParams,
        ResumeParseParams,
        ResumeProfileParams,
        ResumeSetActiveParams,
        ResumeSkillStatusParams,
        SearchProfileParams,
        SearchProfileSaveParams,
        SettingsPatch,
        SleepTaskParams,
        SourceAddParams,
        SourceIdParams,
        SourceSetEnabledParams,
        SourceSetExtractionOrderParams,
        SourceSetScheduleParams,
        SuppressionPolicyParams,
        TaskFilter,
    )

    dispatcher = Dispatcher()
    system = SystemHandlers(
        HelloUseCase(container), HealthUseCase(container), container
    )
    dispatcher.register("system.hello", system.hello, params_model=HelloParams)
    dispatcher.register("system.health", system.health, params_model=EmptyParams)
    dispatcher.register("system.shutdown", system.shutdown, params_model=EmptyParams)
    dispatcher.register(
        "system.export_backup",
        system.export_backup,
        params_model=BackupExportParams,
    )
    dispatcher.register(
        "system.import_backup",
        system.import_backup,
        params_model=BackupImportParams,
    )
    dispatcher.register(
        "system.get_backup_history",
        system.get_backup_history,
        params_model=BackupHistoryParams,
    )

    tasks = TasksHandlers(TasksUseCase(container))
    dispatcher.register("tasks.list", tasks.list, params_model=TaskFilter)
    dispatcher.register("tasks.cancel", tasks.cancel, params_model=CancelTaskParams)

    settings = SettingsHandlers(SettingsUseCase(container))
    dispatcher.register("settings.get", settings.get, params_model=EmptyParams)
    dispatcher.register("settings.update", settings.update, params_model=SettingsPatch)

    plugins = PluginsHandlers(PluginsUseCase(container))
    dispatcher.register("plugins.list", plugins.list, params_model=EmptyParams)
    dispatcher.register("plugins.enable", plugins.enable, params_model=PluginIdParams)
    dispatcher.register("plugins.disable", plugins.disable, params_model=PluginIdParams)
    dispatcher.register(
        "plugins.configure", plugins.configure, params_model=PluginConfigureParams
    )
    dispatcher.register(
        "plugins.get_capabilities",
        plugins.get_capabilities,
        params_model=PluginOptionalIdParams,
    )
    dispatcher.register(
        "plugins.reset_circuit", plugins.reset_circuit, params_model=PluginIdParams
    )

    diagnostics = DiagnosticsHandlers(FetchUrlUseCase(container), container.task_runner)
    dispatcher.register(
        "diagnostics.fetch_url", diagnostics.fetch_url, params_model=FetchUrlParams
    )
    dispatcher.register("diagnostics.sleep", diagnostics.sleep, params_model=SleepTaskParams)

    # M1: discovery + sources + jobs + crawl runs
    discovery = wiring or _build_discovery(container)
    dispatcher.register("sources.list", discovery.sources.list, params_model=EmptyParams)
    dispatcher.register("sources.add", discovery.sources.add, params_model=SourceAddParams)
    dispatcher.register(
        "sources.set_enabled", discovery.sources.set_enabled, params_model=SourceSetEnabledParams
    )
    dispatcher.register(
        "sources.set_extraction_order",
        discovery.sources.set_extraction_order,
        params_model=SourceSetExtractionOrderParams,
    )
    dispatcher.register(
        "sources.set_schedule", discovery.sources.set_schedule, params_model=SourceSetScheduleParams
    )
    dispatcher.register("sources.delete", discovery.sources.delete, params_model=SourceIdParams)

    dispatcher.register("jobs.list", discovery.jobs.list, params_model=JobFilter)
    dispatcher.register("jobs.search", discovery.jobs.search, params_model=JobSearchParams)
    dispatcher.register("jobs.merge", discovery.jobs.merge, params_model=JobMergeParams)
    dispatcher.register("jobs.unmerge", discovery.jobs.unmerge, params_model=JobMergeParams)
    dispatcher.register("jobs.list_duplicates", discovery.jobs.list_duplicates, params_model=EmptyParams)
    dispatcher.register("jobs.list_seen", discovery.jobs.list_seen, params_model=EmptyParams)
    dispatcher.register(
        "jobs.ranked", discovery.jobs.ranked, params_model=RankedJobParams
    )
    dispatcher.register(
        "jobs.list_saved", discovery.jobs.list_saved, params_model=JobsListSavedParams
    )
    dispatcher.register(
        "companies.list",
        discovery.companies.list,
        params_model=EmptyParams,
    )
    dispatcher.register(
        "companies.get_detail",
        discovery.companies.get_detail,
        params_model=CompanyDetailParams,
    )
    dispatcher.register(
        "companies.get_trend",
        discovery.companies.get_trend,
        params_model=CompanyTrendParams,
    )
    dispatcher.register(
        "companies.merge",
        discovery.companies.merge,
        params_model=CompanyMergeParams,
    )
    dispatcher.register(
        "companies.unmerge",
        discovery.companies.unmerge,
        params_model=CompanyDetailParams,
    )
    dispatcher.register(
        "companies.get_merge_history",
        discovery.companies.get_merge_history,
        params_model=CompanyMergeHistoryParams,
    )
    dispatcher.register("jobs.get_detail", discovery.jobs.get_detail, params_model=JobIdParams)
    dispatcher.register(
        "jobs.verify_now", discovery.jobs.verify_now, params_model=JobIdParams
    )

    dispatcher.register("discovery.run", discovery.discovery.run, params_model=DiscoveryRunParams)
    dispatcher.register("discovery.auto_discover", discovery.discovery.auto_discover, params_model=EmptyParams)
    dispatcher.register("discovery.get_status", discovery.discovery.get_status, params_model=EmptyParams)
    dispatcher.register(
        "crawl_runs.list",
        discovery.discovery.list_runs,
        params_model=CrawlRunListParams,
    )

    from app.application.commands.matching import MatchingUseCase
    from app.application.commands.resume import ResumeUseCase
    from app.worker.ipc.handlers.keywords import KeywordsHandlers
    from app.worker.ipc.handlers.matching import MatchingHandlers
    from app.worker.ipc.handlers.resume import ResumeHandlers

    resume_uc = ResumeUseCase(container, Path(container.data_dir))
    matching_uc = MatchingUseCase(container)
    resume = ResumeHandlers(resume_uc)
    matching = MatchingHandlers(matching_uc, container)
    keywords = KeywordsHandlers(container)

    dispatcher.register("resume.import", resume.import_resume, params_model=ResumeImportParams)
    dispatcher.register("resume.list_versions", resume.list_versions, params_model=ResumeProfileParams)
    dispatcher.register("resume.get_profile", resume.get_profile, params_model=ResumeProfileParams)
    dispatcher.register("resume.set_active", resume.set_active, params_model=ResumeSetActiveParams)
    dispatcher.register("resume.update_overrides", resume.update_overrides, params_model=ResumeOverridesParams)
    dispatcher.register("resume.parse", resume.parse, params_model=ResumeParseParams)
    dispatcher.register("resume.set_skill_status", resume.set_skill_status, params_model=ResumeSkillStatusParams)

    dispatcher.register("matching.run", matching.run, params_model=MatchingRunParams)
    dispatcher.register("matching.get_results", matching.get_results, params_model=ResumeProfileParams)
    dispatcher.register("matching.update_weights", matching.update_weights, params_model=MatchingWeightsParams)
    dispatcher.register(
        "matching.get_weight_proposals",
        matching.get_weight_proposals,
        params_model=MatchingWeightProposalsParams,
    )
    dispatcher.register(
        "matching.apply_weight_proposal",
        matching.apply_weight_proposal,
        params_model=MatchingApplyWeightsParams,
    )
    dispatcher.register(
        "matching.get_weight_history",
        matching.get_weight_history,
        params_model=MatchingWeightHistoryParams,
    )
    dispatcher.register("keywords.extract", keywords.extract, params_model=KeywordsExtractParams)
    dispatcher.register(
        "keywords.prevalence",
        keywords.prevalence,
        params_model=KeywordsPrevalenceParams,
    )
    dispatcher.register(
        "keywords.coverage",
        keywords.coverage,
        params_model=KeywordsCoverageParams,
    )
    dispatcher.register(
        "keywords.backfill",
        keywords.backfill,
        params_model=KeywordsBackfillParams,
    )
    dispatcher.register(
        "keywords.trends",
        keywords.trends,
        params_model=KeywordsTrendsParams,
    )

    from app.application.commands.applications import (
        ApplicationsUseCase,
        SuppressionsUseCase,
    )
    from app.worker.ipc.handlers.applications import ApplicationsHandlers

    applications = ApplicationsHandlers(
        ApplicationsUseCase(container), SuppressionsUseCase(container)
    )
    dispatcher.register(
        "applications.create", applications.create, params_model=ApplicationCreateParams
    )
    dispatcher.register(
        "applications.update_status",
        applications.update_status,
        params_model=ApplicationStatusParams,
    )
    dispatcher.register(
        "applications.add_event", applications.add_event, params_model=ApplicationEventParams
    )
    dispatcher.register(
        "applications.record_outcome",
        applications.record_outcome,
        params_model=ApplicationOutcomeParams,
    )
    dispatcher.register(
        "applications.list", applications.list, params_model=ApplicationListParams
    )
    dispatcher.register(
        "applications.get_company_history",
        applications.get_company_history,
        params_model=CompanyHistoryParams,
    )
    dispatcher.register(
        "applications.get_analytics",
        applications.get_analytics,
        params_model=AnalyticsParams,
    )
    dispatcher.register(
        "applications.get_suppressions",
        applications.get_suppressions,
        params_model=EmptyParams,
    )
    dispatcher.register(
        "applications.get_suppression_policy",
        applications.get_suppression_policy,
        params_model=EmptyParams,
    )
    dispatcher.register(
        "applications.restore_suppressed_job",
        applications.restore_suppressed_job,
        params_model=RestoreSuppressedJobParams,
    )
    dispatcher.register(
        "applications.update_suppression_policy",
        applications.update_suppression_policy,
        params_model=SuppressionPolicyParams,
    )
    dispatcher.register(
        "jobs.set_user_state",
        applications.set_user_state,
        params_model=JobUserStateParams,
    )

    from app.application.commands.browser import BrowserUseCase
    from app.worker.ipc.handlers.browser import BrowserHandlers

    browser = BrowserHandlers(BrowserUseCase(container))
    dispatcher.register(
        "browser.open_job", browser.open_job, params_model=BrowserOpenJobParams
    )
    dispatcher.register(
        "browser.inspect_form", browser.inspect_form, params_model=BrowserInspectParams
    )
    dispatcher.register(
        "browser.autofill_plan", browser.autofill_plan, params_model=BrowserPlanParams
    )
    dispatcher.register(
        "browser.autofill", browser.autofill, params_model=BrowserAutofillParams
    )
    dispatcher.register(
        "browser.handoff", browser.handoff, params_model=BrowserHandoffParams
    )
    dispatcher.register(
        "browser.close_session", browser.close_session, params_model=BrowserCloseParams
    )

    from app.application.intelligence import PostingIntelligenceUseCase
    from app.worker.ipc.handlers.posting_intelligence import (
        PostingIntelligenceHandlers,
    )

    intelligence = PostingIntelligenceHandlers(
        PostingIntelligenceUseCase(container), container.task_runner
    )
    dispatcher.register(
        "posting_intelligence.evaluate",
        intelligence.evaluate,
        params_model=PostingIntelligenceEvaluateParams,
    )
    dispatcher.register(
        "posting_intelligence.get_signals",
        intelligence.get_signals,
        params_model=JobIdParams,
    )
    dispatcher.register(
        "posting_intelligence.get_risk_history",
        intelligence.get_risk_history,
        params_model=JobIdParams,
    )
    dispatcher.register(
        "posting_intelligence.get_company_assessment",
        intelligence.get_company_assessment,
        params_model=PostingIntelligenceCompanyParams,
    )
    dispatcher.register(
        "posting_intelligence.configure_model",
        intelligence.configure_model,
        params_model=PostingIntelligenceConfigParams,
    )
    dispatcher.register(
        "posting_intelligence.get_calibration",
        intelligence.get_calibration,
        params_model=EmptyParams,
    )

    profiles = SearchProfilesHandlers(SearchProfilesUseCase(container))
    dispatcher.register("search_profiles.list", profiles.list, params_model=EmptyParams)
    dispatcher.register("search_profiles.save", profiles.save, params_model=SearchProfileSaveParams)
    dispatcher.register("search_profiles.delete", profiles.delete, params_model=SearchProfileParams)
    return dispatcher


@dataclass
class DiscoveryWiring:
    """The M1 discovery pieces, built once and shared by the worker."""

    registry: SourceRegistry
    sources: SourcesHandlers
    jobs: JobsHandlers
    companies: CompaniesHandlers
    discovery: DiscoveryHandlers
    discovery_use_case: DiscoveryUseCase
    auto_discover_use_case: AutoDiscoverUseCase
    scheduler: CrawlScheduler
    crawl_gate: asyncio.Semaphore
    config: Any
    container: Any = None
    policy_http: Any = None

    async def retier(self) -> int:
        """Recompute source tiers now (profile edits, §6.8 / M6)."""
        from app.application.tiers import TierService

        sources = await self.registry.list_sources(enabled_only=True)
        changed = await TierService(self.container).assign(sources)
        return len(changed)

    async def sync_scheduler(self, *, run_immediately: bool = True) -> None:
        """(Re)schedule enabled sources on their effective (tier) cadence.

        The per-source loop is a convenience for a hand-full of boards; the
        discovery passes already crawl every due source.  With a full seed
        registry (tens of thousands of rows) one task per source would cost
        more memory than it returns, so scheduling is capped, manual sources
        first.  Sources beyond the cap are still crawled by the passes.
        """
        enabled = list(await self.registry.list_sources(enabled_only=True))
        manual = [source for source in enabled if "probe_status" not in source.schedule]
        discovered = [
            source for source in enabled if "probe_status" in source.schedule
        ]
        selected = (manual + discovered)[:MAX_SCHEDULED_SOURCES]
        self.scheduler.schedule_sources(
            selected,
            lambda source: self.run_source(source.id),
            run_immediately=run_immediately,
            interval_for=self.interval_for,
        )

    def interval_for(self, source: SourceConfig) -> float:
        """The source's scheduled interval, narrowed by its relevance tier."""
        return effective_crawl_interval_s(
            source, getattr(self.config, "discovery", None)
        )

    def refresh_config(self, config: Any) -> None:
        """Adopt a freshly stored config for the pass and the scheduler."""
        self.config = config
        self.auto_discover_use_case.refresh_config(config)

    async def run_source(self, source_id: str) -> dict[str, object]:
        # The auto-discovery pass and this scheduler share one registry; skip a
        # source the pass refreshed moments ago instead of double-crawling it.
        if not await self.auto_discover_use_case.is_due(source_id):
            return {"source_id": source_id, "skipped": True, "reason": "not_due"}
        # One global gate bounds concurrent crawls across every schedule.
        async with self.crawl_gate:
            return await self.discovery_use_case.run(
                DiscoveryRunParams(source_id=source_id)
            )


def _build_discovery(container: Any) -> DiscoveryWiring:
    # Connectors are plugins: they come from the registry, which also provides
    # the isolation shim used to call them.
    plugin_registry = cast(PluginRegistry, container.registry)
    connectors = [entry.instance for entry in plugin_registry.list_by_kind("connector")]
    cfg = container.config
    policy_http = PolicyHttpClient(
        transport=container.http,
        rate_limiter=RateLimiter(
            min_interval_s=1.0 / max(float(cfg.crawl.per_origin_rps), 1e-6),
            burst=max(1, int(cfg.crawl.burst)),
        ),
        user_agent=cfg.http.user_agent,
        max_retries=int(cfg.crawl.retry.max_attempts),
        base_backoff_s=float(cfg.crawl.retry.base_s),
        cap_backoff_s=float(cfg.crawl.retry.cap_s),
        timeout_s_default=float(cfg.http.timeout_s),
        max_timeout_s=float(cfg.http.max_timeout_s),
        max_bytes=int(cfg.http.max_bytes),
        max_redirects=int(cfg.http.max_redirects),
    )
    registry = SourceRegistry(container.uow_factory, connectors, invoker=plugin_registry)
    discovery_use_case = DiscoveryUseCase(container, registry, policy_http)
    crawl_gate = asyncio.Semaphore(max(1, int(container.config.crawl.global_concurrency)))
    auto_discover_use_case = AutoDiscoverUseCase(
        container, registry, policy_http, crawl_gate=crawl_gate
    )
    return DiscoveryWiring(
        registry=registry,
        sources=SourcesHandlers(SourcesUseCase(container, registry)),
        jobs=JobsHandlers(JobsUseCase(container), container.task_runner),
        companies=CompaniesHandlers(CompaniesUseCase(container)),
        discovery=DiscoveryHandlers(discovery_use_case, auto_discover_use_case),
        discovery_use_case=discovery_use_case,
        auto_discover_use_case=auto_discover_use_case,
        scheduler=CrawlScheduler(),
        crawl_gate=crawl_gate,
        config=container.config,
        policy_http=policy_http,
        container=container,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(prog="lodestar-worker")
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Directory for the worker database and logs",
    )
    parser.add_argument(
        "--no-background-discovery",
        action="store_true",
        help="Do not start discovery/crawling on launch (integration tests)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)

    clock = SystemClock()
    ids = UlidGenerator()
    db_path = data_dir / AppConfig().paths.db_name
    uow_factory = SqliteUnitOfWorkFactory(db_path=db_path, clock_now=clock.now)
    schema_version = await uow_factory.initialize()

    # Tasks and crawl runs left running by a previous process are closed on startup.
    async with uow_factory() as uow:
        await uow.tasks.mark_interrupted()
        await uow.crawl_runs.mark_interrupted()
        await uow.commit()

    cfg = await _load_config(uow_factory, data_dir)
    publisher = QueuedEventPublisher(clock)
    container = build_container(
        cfg,
        overrides={
            "uow_factory": uow_factory,
            "clock": clock,
            "ids": ids,
            "events": publisher,
            "schema_version": schema_version,
        },
        log_dir=data_dir / "logs",
    )
    logger = container.logger
    registry = container.registry
    await registry.load_state()
    await registry.persist_state()

    runner = container.task_runner
    if runner is not None:
        await runner.start()

    # The browser is an optional capability: without Playwright every
    # browser.* call reports an actionable error instead of stopping the worker.
    from dataclasses import replace as dc_replace

    from app.infrastructure.browser import PlaywrightBrowserProvider

    config_state = ConfigState(cfg)
    container = dc_replace(
        container,
        extra={
            **container.extra,
            "browser_provider": PlaywrightBrowserProvider(),
            "config_state": config_state,
        },
    )

    wiring = _build_discovery(container)
    dispatcher = _build_dispatcher(container, wiring)
    # Schedules are restored from the database, but nothing crawls on launch:
    # the first automatic pass (below) crawls what is actually due, bounded.
    await wiring.sync_scheduler(run_immediately=False)

    # Continuous auto-discovery + crawl trigger.  The last completed pass is
    # persisted, so a worker restart resumes the cadence instead of starting a
    # fresh one.  The normal hourly cadence now only starts after the startup
    # backlog drain, which keeps the crawl gate busy instead of waiting a full
    # interval per small batch.
    async def _auto_discover_loop() -> None:
        while True:
            try:
                # Re-read each turn: a settings update changes the pause
                # without a worker restart.
                interval_s = _auto_discover_interval_s(wiring.config)
                result = await wiring.auto_discover_use_case.run_if_due(interval_s)
                if result is not None and (
                    result.registered or result.tiers_changed
                ):
                    # New sources and tier changes both alter the cadence the
                    # scheduler must use; the pass itself already crawled what
                    # was due, so the next run waits a full interval.
                    await wiring.sync_scheduler(run_immediately=False)
            except Exception as exc:  # noqa: BLE001 - the loop must keep running
                logger.warning("discovery.auto_discover.failed", error=str(exc))
            await asyncio.sleep(AUTO_DISCOVER_POLL_S)

    async def _seed_sweep() -> None:
        """Register the rest of the bundled seed in the background at launch."""
        try:
            registered = await wiring.auto_discover_use_case.import_seed()
            if registered:
                # The new boards are crawled by the backlog drain; the
                # per-source loops only need to know about them for the normal
                # cadence.  A scheduler failure must not skip the drain.
                await wiring.sync_scheduler(run_immediately=False)
        except Exception as exc:  # noqa: BLE001 - seeding must not kill the worker
            logger.warning("discovery.seed_import.failed", error=str(exc))

    async def _drain_initial_crawls() -> None:
        """Crawl never-attempted boards back-to-back until the backlog is empty.

        Each batch is larger than the crawl gate, so the gate always has queued
        work.  There is no sleep between batches: the gate and per-origin rate
        limits are the politeness mechanism, not an artificial timer.  Every
        started board writes a crawl run immediately, so an interrupted run
        resumes with exactly the remaining boards on the next launch.
        """
        attempted = 0
        while True:
            try:
                crawled, failed = (
                    await wiring.auto_discover_use_case.crawl_backlog_batch()
                )
            except Exception as exc:  # noqa: BLE001 - the normal loop still runs
                logger.warning("discovery.catch_up.failed", error=str(exc))
                return
            if crawled + failed == 0:
                return
            attempted += crawled + failed
            logger.info(
                "discovery.catch_up.progress",
                attempted=attempted,
                crawled=crawled,
                failed=failed,
            )
            await asyncio.sleep(0)

    async def _initial_pass() -> None:
        """One discovery pass at launch (index queries, tiers, first crawl)."""
        try:
            await wiring.auto_discover_use_case.run(catch_up=True)
        except Exception as exc:  # noqa: BLE001 - crawling must still continue
            logger.warning("discovery.initial_pass.failed", error=str(exc))

    async def _background_discovery() -> None:
        """Seed, start discovery, and drain the first-crawl backlog.

        The seed sweep takes seconds on a first launch (nothing on later
        ones) and registers the whole catalogue offline.  Discovery then runs
        concurrently with the drain: the initial pass looks for new boards and
        crawls what is due while the drain works through every never-attempted
        seed board at the crawl gate's throughput.  The normal hourly cadence
        takes over once the backlog is empty.  No failure here may silently
        skip the pass loop: each stage handles its own error and the loop is
        still entered.
        """
        await _seed_sweep()
        pass_task = asyncio.create_task(_initial_pass())
        try:
            await _drain_initial_crawls()
            await pass_task
        except Exception as exc:  # noqa: BLE001 - the pass loop still runs
            logger.warning("discovery.startup.failed", error=str(exc))
        finally:
            if not pass_task.done():
                pass_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await pass_task
        await _auto_discover_loop()

    async def _maintenance_loop() -> None:
        from app.application.commands.applications import SuppressionsUseCase
        from app.application.endpoint_verifier import EndpointVerifier
        from app.application.intelligence import RiskEvaluationService

        loop = asyncio.get_running_loop()
        next_intelligence_pass = loop.time() + INTELLIGENCE_STARTUP_DELAY_S
        while True:
            try:
                await SuppressionsUseCase(container).sweep()
            except Exception as exc:  # noqa: BLE001 - maintenance must keep running
                logger.warning("suppression.sweep_failed", error=str(exc))
            try:
                from app.application.backup import BackupService

                archived = await BackupService(container).run_if_due()
                if archived is not None:
                    logger.info("backup.scheduled", path=archived.get("path"))
            except Exception as exc:  # noqa: BLE001 - a failed backup is not fatal
                logger.warning("backup.scheduled_failed", error=str(exc))
            if loop.time() >= next_intelligence_pass:
                next_intelligence_pass = loop.time() + INTELLIGENCE_PASS_INTERVAL_S
                # Bounded on every axis: endpoint checks respect the 7-day
                # budget, and risk evaluation reuses identical inputs.
                try:
                    await EndpointVerifier(
                        container, http=wiring.policy_http
                    ).verify(limit=10)
                    await RiskEvaluationService(container).evaluate(
                        scope="active", limit=200
                    )
                except Exception as exc:  # noqa: BLE001 - keep the loop alive
                    logger.warning("intelligence.pass_failed", error=str(exc))
            await asyncio.sleep(MAINTENANCE_POLL_S)

    if args.no_background_discovery:
        discovery_task = asyncio.create_task(asyncio.sleep(0))
    else:
        discovery_task = asyncio.create_task(_background_discovery())

        def _report_background_exit(task: asyncio.Task[None]) -> None:
            """A stopped discovery task must be visible, never silent."""
            if task.cancelled():
                return
            exc = task.exception()
            if exc is not None:
                logger.error("discovery.background_stopped", error=str(exc))

        discovery_task.add_done_callback(_report_background_exit)
    maintenance_task = asyncio.create_task(_maintenance_loop())
    transport = NdjsonTransport(sys.stdin, sys.stdout)
    writer_task = asyncio.create_task(event_writer(publisher.queue, transport.write))
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
    inflight: set[asyncio.Task[None]] = set()

    ready = event_envelope(
        "worker.ready",
        {
            "protocol_versions": [PROTOCOL_VERSION],
            "app_version": container.app_version,
            "schema_version": container.schema_version,
        },
        seq=0,
        ts=clock.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    await transport.write(ready.to_dict())
    logger.info(
        "worker.ready",
        protocol_versions=[PROTOCOL_VERSION],
        app_version=container.app_version,
        schema_version=container.schema_version,
        methods=dispatcher.allowed_methods(),
    )

    async def handle(envelope: dict[str, Any]) -> None:
        async with semaphore:
            try:
                validate_request(envelope)
                response = await dispatcher.handle_request(envelope)
            except PortError as exc:
                response = error_envelope(
                    envelope.get("id") if isinstance(envelope.get("id"), str) else None,
                    exc.code,
                    exc.message,
                    retryable=exc.retryable,
                    details=dict(exc.details),
                ).to_dict()
            await transport.write(response)
            # Sources changed: the schedule may have changed with them.
            # Auto-discovery just crawled every source, so those loops must
            # wait a full interval rather than crawl again immediately.
            method = envelope.get("method")
            if isinstance(method, str) and _response_succeeded(response):
                if method == "sources.add":
                    # The add-source UI runs the first crawl itself. Scheduling
                    # an immediate one too raced it for the same company rows.
                    await wiring.sync_scheduler(run_immediately=False)
                elif method in SCHEDULER_RESYNC_METHODS:
                    await wiring.sync_scheduler()
                elif method == "settings.update":
                    # The pass loop, its budgets and the tier intervals all read
                    # the config they were built with; reload the stored one.
                    # The live holder keeps call-time readers (autofill, the
                    # suppression defaults) in step without a restart.
                    reloaded = await _load_config(uow_factory, data_dir)
                    wiring.refresh_config(reloaded)
                    config_state.set(reloaded)
                    await wiring.sync_scheduler(run_immediately=False)
                elif method in TIER_AFFECTING_METHODS:
                    # A profile/skill edit changes which boards are hot: retier
                    # now and adopt the new cadence without a worker restart.
                    try:
                        changed = await wiring.retier()
                    except Exception as exc:  # noqa: BLE001 - must not fail the edit
                        logger.warning("discovery.tiering_failed", error=str(exc))
                    else:
                        if changed:
                            await wiring.sync_scheduler(run_immediately=False)
                elif method == "discovery.auto_discover" and _scheduler_affected(
                    response
                ):
                    await wiring.sync_scheduler(run_immediately=False)

    shutdown = False
    try:
        while not shutdown:
            try:
                envelope = await transport.read()
            except PortError as exc:
                await transport.write(
                    error_envelope(None, exc.code, exc.message, details=dict(exc.details)).to_dict()
                )
                continue
            if envelope is None:
                break
            if isinstance(envelope, dict) and envelope.get("method") == "system.shutdown":
                # Respond first, then stop accepting requests.
                await transport.write(
                    ok_envelope(
                        envelope.get("id") if isinstance(envelope.get("id"), str) else None,
                        {"ok": True},
                    ).to_dict()
                )
                shutdown = True
                break
            task = asyncio.create_task(handle(envelope))
            inflight.add(task)
            task.add_done_callback(inflight.discard)
            await asyncio.sleep(0)
    finally:
        for task in list(inflight):
            task.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)
        wiring.scheduler.cancel_all()
        discovery_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await discovery_task
        maintenance_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await maintenance_task
        if runner is not None:
            await runner.stop()
        await registry.persist_state()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(publisher.queue.join(), timeout=2)
        writer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await writer_task
        browser_provider = container.extra.get("browser_provider")
        if browser_provider is not None:
            await browser_provider.close_all()
        logger.info("worker.shutdown")
    return 0


def run() -> None:
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":
    run()
