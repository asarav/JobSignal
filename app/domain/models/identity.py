from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class JobFingerprintSet:
    url_key: str
    ats_key: str | None
    company_id: str | None
    title_key: str
    title_family: str | None
    location_key: str | None
    desc_exact_hash: str
    desc_simhash64: int | None = None
    desc_minhash: Sequence[int] = ()
    level: int | None = None
    department: str | None = None
    salary_min: int | None = None
    posted_at: str | None = None
    source_posting_id: str | None = None
    source_id: str | None = None
    req_ids: Sequence[str] = ()
    #: Requirement/preferred skills from the posting's requirement sections.
    #: A change in this set is material even when the description hash matches.
    req_skills: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class SimilarityEvidence:
    score: float
    title_sim: float = 0.0
    location_sim: float = 0.0
    desc_sim: float = 0.0
    dept_sim: float = 0.0
    salary_sim: float = 0.0
    reasons: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class IdentityDecision:
    action: str  # attach_alias | new_snapshot | new_job | link_related
    job_id: str | None
    repost_class: str  # exact_duplicate | reposted_identical | materially_updated | similar_distinct | new_position
    confidence: float
    evidence: Sequence[str] = ()
    related_job_ids: Sequence[str] = ()
    ambiguous: bool = False
    material: bool = False
