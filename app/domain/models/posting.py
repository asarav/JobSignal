"""Posting-quality domain model (spec §9).

Everything here is a pure value: providers and the risk model read a
``JobHistory`` and return new values without touching storage. The models carry
the *why* (``Evidence``/``Explanation``) next to the *what*, because every risk
number has to be reproducible and explainable.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from app.domain.models.common import Evidence, Explanation
from app.domain.models.enums import RiskClass, SignalStatus


@dataclass(frozen=True, slots=True)
class SnapshotPoint:
    """One stored snapshot of a posting, oldest first in a history."""

    at: datetime
    content_hash: str
    title: str = ""
    description_text: str = ""


@dataclass(frozen=True, slots=True)
class ObservationPoint:
    """A crawl observation, e.g. ``seen`` or ``material_change``."""

    at: datetime
    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RepostPoint:
    """A detected repost (spec §7.4) and how it was recognised."""

    at: datetime
    kind: str = "reposted_identical"
    similarity: float = 1.0


@dataclass(frozen=True, slots=True)
class EndpointPoint:
    """One application-endpoint check (spec §9.3, ``signal.endpoint_availability``)."""

    at: datetime
    state: str
    http_status: int | None = None
    final_url: str | None = None


@dataclass(frozen=True, slots=True)
class CohortStats:
    """Median observed posting duration for comparable roles."""

    median_days: float | None = None
    sample_size: int = 0
    scope: str = "none"


@dataclass(frozen=True, slots=True)
class CompanySummaryView:
    """The parts of a company hiring summary a signal may read (spec §9.7)."""

    sample_tier: str = "insufficient"
    n_unique: int = 0
    window_days: float = 0.0
    repost_ratio: float = 0.0
    inactive_ratio: float = 0.0
    median_duration_days: float | None = None


@dataclass(frozen=True, slots=True)
class RelatedJob:
    """Another posting at the same company, for copy detection."""

    job_id: str
    description_similarity: float = 0.0


@dataclass(frozen=True, slots=True)
class JobHistory:
    """Everything the signal providers are allowed to read about one posting."""

    job_id: str
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    snapshot_count: int = 0
    observations: Sequence[Mapping[str, Any]] = ()
    snapshots: Sequence[SnapshotPoint] = ()
    reposts: Sequence[RepostPoint] = ()
    endpoints: Sequence[EndpointPoint] = ()
    related_jobs: Sequence[RelatedJob] = ()
    company_summary: CompanySummaryView | None = None
    cohort: CohortStats | None = None
    statuses_by_source: Mapping[str, str] = field(default_factory=dict)
    field_conflicts: int = 0
    observed_crawl_cycles: int | None = None
    page_text: str = ""
    title: str = ""
    description_text: str = ""
    requirements_text: str = ""

    def observed_days(self, now: datetime) -> float:
        """Days between the first observation and the most recent one."""
        if self.first_seen_at is None:
            return 0.0
        end = self.last_seen_at or now
        return max(0.0, (end - self.first_seen_at).total_seconds() / 86_400)


@dataclass(frozen=True, slots=True)
class SignalContext:
    """Clock and tunables a provider needs beyond the stored history."""

    job_id: str
    now: datetime | None = None
    endpoint_obs_max_age_days: int = 14
    transient_failures_required: int = 2
    cohort_min_sample: int = 15
    company_min_sample: int = 8


@dataclass(frozen=True, slots=True)
class SignalResult:
    """One provider's verdict (spec §9.2). Missing is never negative evidence."""

    signal_id: str
    provider_version: str
    status: SignalStatus
    risk_contribution: float | None
    confidence: float
    strong: bool = False
    evidence: Sequence[Evidence] = ()
    missing_reason: str | None = None
    explanation: str = ""
    description: str = ""
    evidence_source: str = ""
    observed_at: datetime | None = None
    evaluated_at: datetime | None = None

    @property
    def available(self) -> bool:
        return self.status is SignalStatus.AVAILABLE


@dataclass(frozen=True, slots=True)
class RiskThresholds:
    """Documented classification thresholds (spec §9.5)."""

    inactive_risk_min: float = 0.75
    inactive_confidence_min: float = 0.50
    stale_risk_min: float = 0.50
    stale_confidence_min: float = 0.40
    stale_alt_risk_min: float = 0.75
    stale_alt_confidence_min: float = 0.30
    active_risk_max: float = 0.30
    active_confidence_min: float = 0.35
    sufficient_coverage: float = 0.60
    limited_coverage: float = 0.25


@dataclass(frozen=True, slots=True)
class RiskModelConfig:
    """Model identity plus every number that can change a verdict.

    ``fingerprint`` hashes exactly the inputs an assessment depends on, so a
    stored assessment can be re-derived (spec §9.6) and a weight change is
    visible as a new model version rather than a silent drift.
    """

    model_id: str
    model_version: str
    prior_risk: float = 0.20
    prior_weight: float = 0.15
    weights: Mapping[str, float] = field(default_factory=dict)
    thresholds: RiskThresholds = field(default_factory=RiskThresholds)

    def fingerprint(self) -> str:
        payload = {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "prior_risk": self.prior_risk,
            "prior_weight": self.prior_weight,
            "thresholds": asdict(self.thresholds),
            "weights": dict(sorted(self.weights.items())),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()[:16]

    def weight_for(self, signal_id: str) -> float:
        """Weight of ``signal.posting_age`` under the config key ``posting_age``."""
        key = signal_id.removeprefix("signal.")
        return float(self.weights.get(key, 0.0))


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    """A reproducible risk/confidence pair with its evidence (spec §9.4–9.6)."""

    model_id: str
    model_version: str
    risk: float
    confidence: float
    risk_class: RiskClass
    explanation: Explanation
    signals: Sequence[SignalResult] = ()
    evaluated_at: datetime | None = None
    evidence_sufficiency: str = "insufficient"
    config_hash: str = ""
    top_signals: Sequence[str] = ()
