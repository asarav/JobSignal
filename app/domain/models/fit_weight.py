"""Append-only history of accepted fit weights (M10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class FitWeightVersionRecord:
    """One accepted weighting, with the weighting it replaced.

    ``previous_weights`` is ``None`` for the first recorded version, which is the
    built-in default rather than a stored row. The row is never mutated: applying
    another change appends a new version.
    """

    id: str
    profile_id: str
    weights: dict[str, float]
    previous_weights: dict[str, float] | None
    source: str
    created_by: str
    created_at: datetime
    evidence: dict[str, Any] = field(default_factory=dict)
