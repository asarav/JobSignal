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

from app.application.commands.diagnostics import FetchUrlUseCase
from app.application.commands.discovery import DiscoveryUseCase
from app.application.commands.hello import HealthUseCase, HelloUseCase
from app.application.commands.jobs import JobsUseCase
from app.application.commands.plugins import PluginsUseCase
from app.application.commands.search_profiles import SearchProfilesUseCase
from app.application.commands.settings import SettingsUseCase
from app.application.commands.sources import SourcesUseCase
from app.application.commands.tasks import TasksUseCase
from app.application.config import SETTINGS_KEY, AppConfig
from app.application.dto import DiscoveryRunParams
from app.application.orchestration.scheduler import CrawlScheduler
from app.application.sources import SourceRegistry
from app.domain.models.common import PortError
from app.infrastructure.clock import SystemClock
from app.infrastructure.http import PolicyHttpClient
from app.infrastructure.ids import UlidGenerator
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory
from app.plugins.registry import PluginRegistry
from app.worker.composition_root import build_container
from app.worker.ipc.dispatcher import Dispatcher, validate_request
from app.worker.ipc.envelope import PROTOCOL_VERSION, error_envelope, event_envelope, ok_envelope
from app.worker.ipc.events import QueuedEventPublisher, event_writer
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
        CancelTaskParams,
        DiscoveryRunParams,
        EmptyParams,
        FetchUrlParams,
        HelloParams,
        JobFilter,
        JobSearchParams,
        PluginConfigureParams,
        PluginIdParams,
        PluginOptionalIdParams,
        SearchProfileParams,
        SearchProfileSaveParams,
        SettingsPatch,
        SleepTaskParams,
        SourceAddParams,
        SourceIdParams,
        SourceSetEnabledParams,
        SourceSetExtractionOrderParams,
        SourceSetScheduleParams,
        TaskFilter,
    )

    dispatcher = Dispatcher()
    system = SystemHandlers(HelloUseCase(container), HealthUseCase(container))
    dispatcher.register("system.hello", system.hello, params_model=HelloParams)
    dispatcher.register("system.health", system.health, params_model=EmptyParams)
    dispatcher.register("system.shutdown", system.shutdown, params_model=EmptyParams)

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

    dispatcher.register("discovery.run", discovery.discovery.run, params_model=DiscoveryRunParams)
    dispatcher.register("crawl_runs.list", discovery.discovery.list_runs, params_model=EmptyParams)

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
    discovery: DiscoveryHandlers
    discovery_use_case: DiscoveryUseCase
    scheduler: CrawlScheduler

    async def sync_scheduler(self) -> None:
        """(Re)schedule every enabled source that declares an interval."""
        enabled = await self.registry.list_sources(enabled_only=True)
        self.scheduler.schedule_sources(
            list(enabled), lambda source: self.run_source(source.id)
        )

    async def run_source(self, source_id: str) -> dict[str, object]:
        return await self.discovery_use_case.run(DiscoveryRunParams(source_id=source_id))


def _build_discovery(container: Any) -> DiscoveryWiring:
    # Connectors are plugins: they come from the registry, which also provides
    # the isolation shim used to call them.
    plugin_registry = cast(PluginRegistry, container.registry)
    connectors = [entry.instance for entry in plugin_registry.list_by_kind("connector")]
    policy_http = PolicyHttpClient(transport=container.http)
    registry = SourceRegistry(container.uow_factory, connectors, invoker=plugin_registry)
    discovery_use_case = DiscoveryUseCase(container, registry, policy_http)
    return DiscoveryWiring(
        registry=registry,
        sources=SourcesHandlers(SourcesUseCase(container, registry)),
        jobs=JobsHandlers(JobsUseCase(container)),
        discovery=DiscoveryHandlers(discovery_use_case),
        discovery_use_case=discovery_use_case,
        scheduler=CrawlScheduler(),
    )


async def main() -> int:
    parser = argparse.ArgumentParser(prog="lodestar-worker")
    parser.add_argument(
        "--data-dir",
        required=True,
        help="Directory for the worker database and logs",
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

    wiring = _build_discovery(container)
    dispatcher = _build_dispatcher(container, wiring)
    await wiring.sync_scheduler()
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
            method = envelope.get("method")
            if isinstance(method, str) and method.startswith("sources."):
                await wiring.sync_scheduler()

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
        if runner is not None:
            await runner.stop()
        await registry.persist_state()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(publisher.queue.join(), timeout=2)
        writer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await writer_task
        logger.info("worker.shutdown")
    return 0


def run() -> None:
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":
    run()
