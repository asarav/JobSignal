"""Backup audit records (M11)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class BackupRunRecord:
    """One export or import, recorded append-only."""

    id: str
    kind: str
    path: str
    checksum: str
    schema_version: int
    app_version: str
    created_at: datetime
    table_counts: dict[str, int] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)
