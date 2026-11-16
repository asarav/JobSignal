"""Test-only container builders with fakes (M0).

Production wiring lives in ``app.worker.composition_root``. Tests reuse that
composition root with explicit overrides instead of hand-building containers,
so the swapability suite exercises the same assembly path the desktop app uses.
"""

from __future__ import annotations

from typing import Any

from app.application.config import AppConfig
from app.application.container import Container
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory
from app.worker.composition_root import build_container
from tests.support.fakes import (
    FakeClock,
    FakeEventPublisher,
    FakeIdGenerator,
    FakeStructuredLogger,
)


def build_test_container(
    *,
    data_dir: str = "lodestar-test-data",
    sqlite_db: Any = None,
    uow_factory: Any = None,
    http: Any = None,
    registry: Any = None,
    events: Any = None,
    clock: Any = None,
    config: AppConfig | None = None,
) -> Container:
    """Build a real container with test doubles injected through overrides."""
    cfg = (config or AppConfig()).with_data_dir(data_dir)
    overrides: dict[str, Any] = {
        "clock": clock or FakeClock(),
        "ids": FakeIdGenerator(),
        "logger": FakeStructuredLogger(),
        "events": events or FakeEventPublisher(),
        "http": http,
        "registry": registry,
    }
    if uow_factory is None:
        uow_factory = (
            SqliteUnitOfWorkFactory(db_path=sqlite_db, clock_now=(clock or FakeClock()).now)
            if sqlite_db is not None
            else InMemoryUnitOfWorkFactory()
        )
    overrides["uow_factory"] = uow_factory
    return build_container(cfg, overrides=overrides, log_dir=None)
