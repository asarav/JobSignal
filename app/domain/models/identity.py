from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JobFingerprintSet:
    url_key: str
    ats_key: str | None
    company_id: str | None
    title_key: str
    location_key: str | None
    desc_exact_hash: str
    desc_simhash64: int | None = None
    algo_version: str = "fp.v1"


@dataclass(frozen=True, slots=True)
class SimilarityEvidence:
    score: float
    reasons: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class IdentityDecision:
    decision: str
    job_id: str | None
    confidence: float
    evidence: Sequence[str] = ()
    ambiguous: bool = False
