from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.domain.models.common import Provenance
from app.domain.models.company import CompanyRef
from app.domain.models.enums import (
    EmploymentType,
    ExtractionStatus,
    PostingStatus,
    Seniority,
    WorkplaceType,
)


@dataclass(frozen=True, slots=True)
class LocationRef:
    raw: str
    city: str | None = None
    region: str | None = None
    country: str | None = None
    lat: float | None = None
    lon: float | None = None


@dataclass(frozen=True, slots=True)
class SalaryRange:
    min_amount: int | None
    max_amount: int | None
    currency: str | None
    period: str | None
    annualized_min: int | None
    annualized_max: int | None
    raw_text: str | None


@dataclass(frozen=True, slots=True)
class RawJobRef:
    source_id: str
    url: str
    source_posting_id: str | None = None
    updated_at_hint: str | None = None


@dataclass(frozen=True, slots=True)
class RawJob:
    ref: RawJobRef
    title: str | None
    body_text: str | None
    body_html: str | None
    payload: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NormalizedJob:
    company_ref: CompanyRef
    title: str
    normalized_title: str
    title_family: str | None
    description_text: str
    description_html: str | None
    locations: Sequence[LocationRef]
    workplace_type: WorkplaceType
    employment_type: EmploymentType
    salary: SalaryRange | None
    posted_at: datetime | None
    source_id: str
    source_posting_id: str | None
    canonical_url: str
    original_urls: Sequence[str]
    department: str | None
    seniority: Seniority
    required_skills: Sequence[str]
    preferred_skills: Sequence[str]
    extraction_status: ExtractionStatus
    extraction_confidence: float
    field_confidence: Mapping[str, float]
    provenance: Sequence[Provenance]
    posting_status_hint: PostingStatus
    fetched_at: datetime


@dataclass(frozen=True, slots=True)
class JobView:
    id: str
    title: str
    company_id: str
    company_name: str
    canonical_url: str
    description_text: str
    workplace_type: WorkplaceType
    employment_type: EmploymentType
    seniority: Seniority
    locations: Sequence[LocationRef] = ()
    required_skills: Sequence[str] = ()
    preferred_skills: Sequence[str] = ()
    posted_at: datetime | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    current_snapshot_id: str | None = None
    posting_status: PostingStatus = PostingStatus.UNKNOWN
