from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, TypeVar

T = TypeVar("T")
E = TypeVar("E")


@dataclass(frozen=True, slots=True)
class Ok[T]:
    value: T


@dataclass(frozen=True, slots=True)
class Err[E]:
    error: E


Result = Ok[T] | Err[E]


@dataclass(slots=True)
class PortError(Exception):
    code: str
    message: str
    retryable: bool = False
    details: Mapping[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass(frozen=True, slots=True)
class Provenance:
    source_id: str
    strategy_id: str
    plugin_id: str
    plugin_version: str
    url: str
    fetched_at: datetime
    content_hash: str | None
    notes: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: str
    description: str
    data: Mapping[str, Any]
    observed_at: datetime
    source_ref: str | None = None


@dataclass(frozen=True, slots=True)
class Reason:
    code: str
    message: str
    weight: float | None = None
    evidence: Sequence[Evidence] = ()


@dataclass(frozen=True, slots=True)
class Explanation:
    summary: str
    reasons: Sequence[Reason]
