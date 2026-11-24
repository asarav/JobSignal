"""Composition root: builds an immutable Container from typed AppConfig.

Exactly one place in the worker constructs concrete adapters. No service
locator, no global singletons, no import-time side effects.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from app.application.config import AppConfig
from app.application.container import Container
from app.application.extraction import ExtractionPipeline
from app.application.orchestration.task_runner import TaskRunner
from app.domain.ports.logging import StructuredLogger
from app.infrastructure.clock import SystemClock
from app.infrastructure.http.httpx_client import HttpxClient
from app.infrastructure.ids import UlidGenerator
from app.infrastructure.logging import configure_logging
from app.infrastructure.persistence.plugin_state import make_plugin_state_repository
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory
from app.plugins._builtin import register_builtin_plugins
from app.plugins.registry import PluginDeps, PluginRegistry, make_registry


class _LoggingEventPublisher:
    """Fallback publisher for non-IPC contexts (tests, headless use)."""

    def __init__(self, logger: StructuredLogger) -> None:
        self._logger = logger

    def publish(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        task_id: str | None = None,
    ) -> None:
        self._logger.info("event.published", topic=topic, task_id=task_id, payload=payload)


def build_extraction_pipeline(registry: PluginRegistry) -> ExtractionPipeline:
    """Build the extraction pipeline from the registered extraction plugins.

    Smoke-test strategies are registered but never run, so they are skipped.
    """
    strategies = [
        entry.instance
        for entry in registry.list_by_kind("extraction")
        if not getattr(entry.instance, "excluded_from_pipeline", False)
    ]
    return ExtractionPipeline(strategies)


def build_container(
    cfg: AppConfig,
    *,
    overrides: dict[str, Any] | None = None,
    log_dir: str | Path | None = None,
) -> Container:
    overrides = overrides or {}
    clock = overrides.get("clock") or SystemClock()
    ids = overrides.get("ids") or UlidGenerator()
    logger = overrides.get("logger") or configure_logging(
        level=cfg.logging.level,
        log_dir=log_dir,
        rotate_files=cfg.logging.rotate_files,
        rotate_mb=cfg.logging.rotate_mb,
    )

    uow_factory = overrides.get("uow_factory")
    if uow_factory is None:
        data_dir = Path(cfg.paths.data_dir) if cfg.paths.data_dir else Path.cwd()
        db_path = data_dir / cfg.paths.db_name
        uow_factory = SqliteUnitOfWorkFactory(db_path=db_path, clock_now=clock.now)

    http = overrides.get("http")
    if http is None:
        http = HttpxClient(cfg.http)

    # Optional browser provider for rendered-DOM extraction (M2). Playwright is
    # an optional dependency, so this stays None when it is not installed.
    browser = overrides.get("browser")
    if browser is None:
        try:
            from app.infrastructure.browser.playwright import PlaywrightBrowserProvider

            browser = PlaywrightBrowserProvider()
        except Exception:  # noqa: BLE001 - playwright may not be installed
            browser = None

    state_repo = overrides.get("plugin_state_repo")
    if state_repo is None and overrides.get("registry") is None:
        state_repo = make_plugin_state_repository(uow_factory)

    registry = overrides.get("registry")
    if registry is None:
        registry = make_registry(
            state_repo=state_repo,
            logger=logger,
            default_timeout_s=cfg.plugins.call_timeout_s,
        )
        register_builtin_plugins(
            registry,
            PluginDeps(
                uow_factory=uow_factory,
                clock=clock,
                http=http,
                logger=logger,
                config=cfg,
                browser=browser,
            ),
        )

    events = overrides.get("events") or _LoggingEventPublisher(logger)

    backup_store = overrides.get("backup_store")
    if backup_store is None:
        from app.infrastructure.persistence.memory.backups import (
            InMemoryBackupStore,
        )
        from app.infrastructure.persistence.sqlite.backups import (
            SqliteBackupStore,
        )
        if isinstance(uow_factory, SqliteUnitOfWorkFactory):
            backup_store = SqliteBackupStore(
                uow_factory.db_path, data_dir=cfg.paths.data_dir
            )
        else:
            backup_store = InMemoryBackupStore(data_dir=cfg.paths.data_dir)

    base = Container(
        config=cfg,
        clock=clock,
        ids=ids,
        http=http,
        uow_factory=uow_factory,
        events=events,
        logger=logger,
        registry=registry,
        data_dir=cfg.paths.data_dir,
        schema_version=overrides.get("schema_version", 1),
        app_version=cfg.app_version,
        protocol_version=cfg.protocol_version,
        extraction_pipeline=build_extraction_pipeline(registry),
        extra={"browser": browser},
        backup_store=backup_store,
    )
    runner = TaskRunner(base)
    return replace(base, task_runner=runner)


__all__ = ["PluginRegistry", "build_container"]
