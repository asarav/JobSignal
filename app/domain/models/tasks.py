from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.models.enums import TaskStatus


@dataclass(frozen=True, slots=True)
class BackgroundTask:
    id: str
    type: str
    status: TaskStatus
    priority: int = 5
    params: Mapping[str, object] = field(default_factory=dict)
    progress: Mapping[str, object] = field(default_factory=dict)
    error: Mapping[str, object] | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    parent_task_id: str | None = None
