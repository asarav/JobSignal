from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol


class EventPublisher(Protocol):
    def publish(
        self,
        topic: str,
        payload: Mapping[str, Any],
        *,
        task_id: str | None = None,
    ) -> None: ...
