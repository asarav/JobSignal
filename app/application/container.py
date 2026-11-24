"""Immutable application container built once by the worker composition root.

Use cases receive the container (or a typed subset) through constructor
injection. There is no service locator, no global state, and nothing is
constructed at import time.

``registry`` is typed as the domain port so ``app.application`` never imports
``app.plugins`` (enforced by import-linter).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.domain.ports.clock import Clock, IdGenerator
from app.domain.ports.events import EventPublisher
from app.domain.ports.http import HttpClient
from app.domain.ports.logging import StructuredLogger
from app.domain.ports.plugins import PluginRegistryPort
from app.domain.ports.repositories import UnitOfWorkFactory

if TYPE_CHECKING:
    from app.application.orchestration.task_runner import TaskRunner


@dataclass(frozen=True, slots=True)
class Container:
    """Typed bag of cross-cutting dependencies.

    Frozen on purpose: after ``build_container`` returns, the graph is fixed.
    Long-running collaborators (``task_runner``) are attached with
    ``dataclasses.replace`` during composition.
    """

    config: Any
    clock: Clock
    ids: IdGenerator
    http: HttpClient
    uow_factory: UnitOfWorkFactory
    events: EventPublisher
    logger: StructuredLogger
    registry: PluginRegistryPort
    data_dir: str = ""
    schema_version: int = 1
    app_version: str = "0.0.0"
    protocol_version: int = 1
    task_runner: TaskRunner | None = None
    #: The extraction pipeline (M2). Built by the composition root from the
    #: registered ``extraction`` plugins.
    extraction_pipeline: Any = None
    extra: dict[str, Any] = field(default_factory=dict)
    #: Portable backup archive adapter (M11). ``None`` when the data store
    #: cannot provide one.
    backup_store: Any = None
