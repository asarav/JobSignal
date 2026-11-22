"""Application history and company-aware suppression (spec section 8, M7).

An application is a *user* record: it keeps the job title, description and
snapshot the user applied to, so later postings and expiries are explained
against what was actually applied for rather than whatever the board shows
today. Its event log is append-only.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

APPLICATION_STATUSES: tuple[str, ...] = (
    "interested",
    "saved",
    "applied",
    "acknowledged",
    "recruiter_contacted",
    "interviewing",
    "rejected",
    "withdrawn",
    "offered",
    "accepted",
)

#: Pre-application states. They never set ``applied_at`` and never start a
#: cooldown (spec section 8.2 rule 1 -- test-enforced).
PRE_APPLICATION_STATUSES: tuple[str, ...] = ("interested", "saved")

OUTCOME_KINDS: tuple[str, ...] = (
    "no_response",
    "acknowledged",
    "recruiter_contact",
    "interview",
    "rejection",
    "offer",
    "withdrawn",
    "accepted",
)

SUPPRESSION_STATES: tuple[str, ...] = ("active", "expired", "restored")

JOB_USER_STATES: tuple[str, ...] = ("new", "saved", "dismissed", "hidden")


@dataclass(frozen=True, slots=True)
class Application:
    id: str
    company_id: str
    job_id: str
    job_title: str
    job_title_family: str | None
    job_canonical_url: str
    job_description_text: str
    job_department: str | None
    job_locations: Sequence[Mapping[str, object]]
    job_workplace_type: str
    job_snapshot_id: str | None
    status: str
    applied_at: datetime | None
    resume_version_id: str | None
    notes: str
    created_at: datetime
    updated_at: datetime

    @property
    def is_applied(self) -> bool:
        return self.applied_at is not None


@dataclass(frozen=True, slots=True)
class ApplicationEvent:
    id: str
    application_id: str
    kind: str
    at: datetime
    from_status: str | None = None
    to_status: str | None = None
    note: str = ""
    data: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ApplicationOutcome:
    id: str
    application_id: str
    kind: str
    occurred_at: datetime
    note: str = ""
    proposed: bool = False
    confirmed: bool = True
    created_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SuppressionPolicy:
    """The stored policy; defaults come from ``AppConfig.suppression``."""

    exact_repost_suppress: bool = True
    similar_role_cooldown_days: int = 180
    similar_role_threshold: float = 0.75
    company_wide_cooldown_enabled: bool = False
    company_wide_cooldown_days: int = 90
    different_role_same_company: str = "allow"
    re_evaluate_on_expiry: bool = True

    def to_mapping(self) -> dict[str, object]:
        return {
            "exact_repost_suppress": self.exact_repost_suppress,
            "similar_role_cooldown_days": self.similar_role_cooldown_days,
            "similar_role_threshold": self.similar_role_threshold,
            "company_wide_cooldown_enabled": self.company_wide_cooldown_enabled,
            "company_wide_cooldown_days": self.company_wide_cooldown_days,
            "different_role_same_company": self.different_role_same_company,
            "re_evaluate_on_expiry": self.re_evaluate_on_expiry,
        }

    @classmethod
    def from_mapping(cls, values: Mapping[str, object] | None) -> SuppressionPolicy:
        values = values or {}

        def _bool(key: str, default: bool) -> bool:
            raw = values.get(key)
            return raw if isinstance(raw, bool) else default

        def _int(key: str, default: int) -> int:
            raw = values.get(key)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return default
            return max(0, int(raw))

        def _float(key: str, default: float) -> float:
            raw = values.get(key)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                return default
            return min(1.0, max(0.0, float(raw)))

        return cls(
            exact_repost_suppress=_bool("exact_repost_suppress", True),
            similar_role_cooldown_days=_int("similar_role_cooldown_days", 180),
            similar_role_threshold=_float("similar_role_threshold", 0.75),
            company_wide_cooldown_enabled=_bool(
                "company_wide_cooldown_enabled", False
            ),
            company_wide_cooldown_days=_int("company_wide_cooldown_days", 90),
            different_role_same_company=str(
                values.get("different_role_same_company") or "allow"
            ),
            re_evaluate_on_expiry=_bool("re_evaluate_on_expiry", True),
        )


@dataclass(frozen=True, slots=True)
class SuppressionDecision:
    reason: str
    explanation: str
    similarity: float
    expires_at: datetime | None
    evidence: Mapping[str, object] = field(default_factory=dict)
    application_id: str = ""
    policy: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SuppressionRecord:
    id: str
    job_id: str
    application_id: str
    state: str
    reason: str
    explanation: str
    similarity: float
    evidence: Mapping[str, object]
    policy: Mapping[str, object]
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class SuppressionCandidate:
    """The part of an incoming job that suppression evaluates."""

    job_id: str
    company_id: str
    title: str
    title_family: str | None
    description_text: str
    canonical_url: str
    department: str | None = None
    locations: Sequence[Mapping[str, object]] = ()
    workplace_type: str = "unknown"
    related_job_ids: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class AppliedJob:
    """The part of an application suppression evaluates against."""

    application_id: str
    job_id: str
    company_id: str
    title: str
    title_family: str | None
    description_text: str
    canonical_url: str
    applied_at: datetime
    department: str | None = None
    locations: Sequence[Mapping[str, object]] = ()
    workplace_type: str = "unknown"
    related_job_ids: Sequence[str] = ()
