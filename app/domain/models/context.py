from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.domain.ports.clock import Clock
    from app.domain.ports.http import HttpClient


class StructuredLogger(Protocol):
    def bind(self, **fields: Any) -> StructuredLogger: ...

    def debug(self, event: str, **fields: Any) -> None: ...

    def info(self, event: str, **fields: Any) -> None: ...

    def warning(self, event: str, **fields: Any) -> None: ...

    def error(self, event: str, **fields: Any) -> None: ...


class CancelledError(Exception):
    """Cooperative cancellation requested."""


class CancellationToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled

    def throw_if_cancelled(self) -> None:
        if self._cancelled:
            raise CancelledError("task cancelled")


@dataclass(frozen=True, slots=True)
class ResourceBudget:
    max_requests: int = 100
    max_bytes: int = 50_000_000
    max_duration_s: float = 300.0


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    task_id: str
    clock: Clock
    http: HttpClient
    logger: StructuredLogger
    cancel: CancellationToken
    deadline: datetime | None
    config: Mapping[str, Any] = field(default_factory=dict)
    budgets: ResourceBudget = field(default_factory=ResourceBudget)
