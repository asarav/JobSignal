from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class OpenOptions:
    headed: bool = True
    timeout_s: float = 30.0


@dataclass(frozen=True, slots=True)
class BrowserPage:
    session_id: str
    url: str


@dataclass(frozen=True, slots=True)
class FormField:
    name: str
    field_type: str
    label: str | None = None
    classification: str = "unknown"
    confidence: float = 0.0
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class FormModel:
    session_id: str
    url: str
    fields: Sequence[FormField] = ()


@dataclass(frozen=True, slots=True)
class AnswerBook:
    answers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PlannedFill:
    field_name: str
    value: str
    source: str
    action: str  # fillable | skipped | blocked


@dataclass(frozen=True, slots=True)
class FillPlan:
    plan_id: str
    session_id: str
    items: Sequence[PlannedFill] = ()


@dataclass(frozen=True, slots=True)
class FillReport:
    plan_id: str
    filled: Sequence[str] = ()
    skipped: Sequence[str] = ()
    blocked: Sequence[str] = ()
