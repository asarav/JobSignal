from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class CompanyRef:
    name: str
    domains: Sequence[str] = ()
    ats_tenant_keys: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class Company:
    id: str
    canonical_name: str
    normalized_name: str
    domains: Sequence[str] = ()
    ats_tenants: Sequence[str] = ()
    aliases: Sequence[str] = ()
    notes: str | None = None
    created_at: datetime | None = None
    website: str | None = None
