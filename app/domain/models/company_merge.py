"""Company identity records (M9).

A merge is a decision, not a mutation: the audit row says which company was
absorbed into which survivor at what time. Reads derive the current view from the
newest decision per merged company.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class CompanyMergeRecord:
    id: str
    survivor_id: str
    merged_id: str
    action: str
    created_at: datetime
    reason: str = ""
    created_by: str = "user"
