from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class ConnectorCapabilities:
    list_jobs: bool = True
    get_job: bool = True
    supports_pagination: bool = False
    requires_rendering: bool = False
    network_hosts: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class PageSniff:
    url: str
    html_excerpt: str | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MatchResult:
    matched: bool
    confidence: float
    ats_hint: str | None = None


@dataclass(frozen=True, slots=True)
class SourceConfig:
    id: str
    url: str
    kind: str
    company_id: str | None = None
    connector_id: str | None = None
    ats_tenant_key: str | None = None
    enabled: bool = True
    extraction_order: Sequence[str] = ()
    limits: Mapping[str, object] = field(default_factory=dict)
    schedule: Mapping[str, object] = field(default_factory=dict)
    #: Archived sources keep their jobs, observations and crawl history but are
    #: hidden from every list; ``sources.delete`` archives (§15.4).
    archived: bool = False
