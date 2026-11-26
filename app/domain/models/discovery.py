from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from app.domain.models.common import Evidence


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    offline: bool = False
    network_hosts: Sequence[str] = ()
    requires_opt_in: bool = False


@dataclass(frozen=True, slots=True)
class DiscoveryQuery:
    text: str | None = None
    company_names: Sequence[str] = ()
    urls: Sequence[str] = ()
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DiscoveredSource:
    kind: str
    url: str
    company_hint: str | None
    ats_hint: str | None
    confidence: float
    evidence: Sequence[Evidence]
    #: ATS tenant key when the URL alone does not carry it.  Workday keeps the
    #: tenant in the subdomain but its CxS API also needs the board site, so a
    #: discovered Workday board records the tenant explicitly.
    tenant_key: str | None = None
    #: Posting count observed by whoever proposed the board (the seed shards
    #: carry it; a probe fills it in for live discovery).  ``None`` means "not
    #: observed", which is different from an observed zero.
    observed_postings: int | None = None


@dataclass(frozen=True, slots=True)
class DiscoveredPosting:
    """A posting URL harvested from the same web index that lists boards.

    Harvested postings are candidates only: the funnel verifies each one with a
    single fetch before it becomes a job, and it never drives ``not_seen`` or
    removal logic (a full listing remains the source of truth for that).
    """

    url: str
    board_url: str
    connector_id: str | None = None
    source_posting_id: str | None = None
    company_hint: str | None = None
    tenant_key: str | None = None
    confidence: float = 0.5
    evidence: Sequence[Evidence] = ()
