"""Identity resolution wiring for ingestion (spec §7.3 / §7.4).

The domain service :func:`app.domain.services.identity.resolve` is pure.  This
module builds the fingerprints it consumes from a normalized posting and from
stored rows, and persists the decision (fingerprint history and repost event)
through the ``dedup`` repository.

Ingestion calls it only when a posting is *not* already known by URL or
``(source, source_posting_id)``; that is exactly the repost case the spec
describes: same job, new URL/ATS id.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.domain.models.identity import IdentityDecision, JobFingerprintSet
from app.domain.models.job import NormalizedJob
from app.domain.services.fingerprints import (
    ats_key,
    desc_exact_hash,
    location_key,
    minhash_signature,
    simhash64,
    title_key,
    url_key,
)
from app.domain.services.identity import resolve

#: Version of the fingerprint computation stored with every row.
ALGO_VERSION = "fingerprints.v1"

#: Fingerprint comparison is bounded per company by this many recent postings.
CANDIDATE_LIMIT = 200


@dataclass(frozen=True, slots=True)
class Candidate:
    """A stored fingerprint together with the job it belongs to."""

    job_id: str
    fingerprint: JobFingerprintSet


def build_fingerprint(
    job: NormalizedJob,
    *,
    company_id: str | None,
    ats_id: str | None,
    tenant_key: str | None,
) -> JobFingerprintSet:
    """The §7.2 fingerprint set for an incoming normalized posting."""
    locations = [
        location.raw or " ".join(part for part in (location.city, location.region) if part)
        for location in job.locations
    ]
    salary_min = None
    if job.salary is not None:
        salary_min = job.salary.annualized_min or job.salary.min_amount
    return JobFingerprintSet(
        url_key=url_key(job.canonical_url),
        ats_key=ats_key(ats_id, tenant_key, job.source_posting_id),
        company_id=company_id,
        title_key=title_key(job.normalized_title or job.title),
        title_family=job.title_family,
        location_key=location_key(locations, job.workplace_type.value),
        desc_exact_hash=desc_exact_hash(job.description_text),
        desc_simhash64=simhash64(job.description_text),
        desc_minhash=tuple(minhash_signature(job.description_text)),
        department=job.department,
        salary_min=salary_min,
        posted_at=job.posted_at.isoformat() if job.posted_at else None,
        source_posting_id=job.source_posting_id,
        source_id=job.source_id,
        req_skills=tuple(
            sorted(
                {
                    skill.strip().lower()
                    for skill in (*job.required_skills, *job.preferred_skills)
                    if skill and skill.strip()
                }
            )
        ),
    )


def fingerprint_from_row(row: Mapping[str, Any]) -> JobFingerprintSet:
    """Rebuild a stored fingerprint from a ``job_fingerprints`` join row."""
    salary = _json(row.get("salary_json"))
    salary_min = None
    if isinstance(salary, Mapping):
        value = salary.get("annualized_min") or salary.get("min_amount")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            salary_min = int(value)
    return JobFingerprintSet(
        url_key=str(row.get("url_key") or url_key(str(row.get("canonical_url") or ""))),
        ats_key=_optional_str(row.get("ats_key")),
        company_id=_optional_str(row.get("company_id")),
        title_key=str(row.get("title_key") or ""),
        title_family=_optional_str(row.get("title_family")),
        location_key=_optional_str(row.get("location_key")),
        desc_exact_hash=str(row.get("desc_exact_hash") or ""),
        desc_simhash64=_optional_int(row.get("desc_simhash64")),
        desc_minhash=_minhash(row.get("desc_minhash")),
        department=_optional_str(row.get("department")),
        salary_min=salary_min,
        posted_at=_optional_str(row.get("posted_at")),
        source_posting_id=_optional_str(row.get("source_posting_id")),
        source_id=_optional_str(row.get("source_id")),
        req_skills=_skills(row),
    )


async def resolve_incoming(
    uow: Any,
    incoming: JobFingerprintSet,
    *,
    thresholds: Mapping[str, float] | None = None,
    weights: Mapping[str, float] | None = None,
    material_change_delta: float = 0.10,
    limit: int = CANDIDATE_LIMIT,
) -> tuple[IdentityDecision, list[Candidate]]:
    """Resolve an unknown posting against the same company's recent jobs.

    Returns the decision plus the candidates it was made against, so the
    caller can map ``related_job_ids`` (URL keys) back to real job ids.
    """
    if not incoming.company_id:
        return (
            IdentityDecision(
                action="new_job",
                job_id=None,
                repost_class="new_position",
                confidence=1.0,
                evidence=("no company to block candidates against",),
            ),
            [],
        )
    rows = await uow.dedup.candidate_fingerprints(
        incoming.company_id, limit=limit
    )
    candidates: list[Candidate] = []
    for row in rows:
        job_id = str(row.get("job_id") or "")
        if not job_id:
            continue
        fingerprint = fingerprint_from_row(row)
        if fingerprint.url_key == incoming.url_key:
            continue
        candidates.append(Candidate(job_id=job_id, fingerprint=fingerprint))
    decision = resolve(
        incoming,
        [candidate.fingerprint for candidate in candidates],
        thresholds=dict(thresholds) if thresholds is not None else None,
        weights=dict(weights) if weights is not None else None,
        material_change_delta=material_change_delta,
    )
    return decision, candidates


def related_candidate(
    decision: IdentityDecision, candidates: Sequence[Candidate]
) -> Candidate | None:
    """The candidate a decision's ``related_job_ids`` points at."""
    wanted = set(decision.related_job_ids)
    for candidate in candidates:
        if candidate.fingerprint.url_key in wanted:
            return candidate
    return None


async def record_fingerprint(
    uow: Any,
    *,
    job_id: str,
    fingerprint: JobFingerprintSet,
    now: datetime,
) -> None:
    """Append the fingerprint set for a job's current content."""
    await uow.dedup.add_fingerprint(
        job_id,
        fingerprint,
        snapshot_id=None,
        created_at=now,
    )


async def record_repost_event(
    uow: Any,
    *,
    job_id: str,
    decision: IdentityDecision,
    incoming: JobFingerprintSet,
    previous: JobFingerprintSet | None,
    now: datetime,
) -> None:
    """Persist a non-trivial identity decision (spec §7.4).

    ``new_position`` is the ordinary path and produces no event.  A
    ``similar_distinct`` posting is related to its candidate but never merged.
    """
    if decision.repost_class == "new_position":
        return
    await uow.dedup.add_repost_event(
        job_id=job_id,
        previous_alias_id=previous.url_key if previous is not None else None,
        new_alias_id=incoming.url_key,
        detected_at=now,
        repost_class=decision.repost_class,
        confidence=float(decision.confidence),
        url_changed=(
            previous is not None and previous.url_key != incoming.url_key
        ),
        ats_id_changed=(
            previous is not None
            and incoming.ats_key is not None
            and previous.ats_key is not None
            and incoming.ats_key != previous.ats_key
        ),
        posted_date_reset=_posted_date_reset(previous, incoming),
        material=bool(decision.material),
        evidence=tuple(decision.evidence),
    )


def _posted_date_reset(
    previous: JobFingerprintSet | None, incoming: JobFingerprintSet
) -> bool:
    """True when the posting date moved later while the text is identical.

    Spec §7.4: a reset is a new ``posted_at`` later than the prior one on
    identical content — the signature of a recycled posting.  If either side
    lacks a posted date, or the content changed, this is not a reset.
    """
    if previous is None or not previous.posted_at or not incoming.posted_at:
        return False
    if previous.desc_exact_hash != incoming.desc_exact_hash:
        return False
    try:
        before = datetime.fromisoformat(previous.posted_at.replace("Z", "+00:00"))
        after = datetime.fromisoformat(incoming.posted_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return after > before


def _json(raw: object) -> Any:
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8", "replace")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def _minhash(raw: object) -> tuple[int, ...]:
    value = _json(raw)
    if not isinstance(value, list):
        return ()
    return tuple(int(item) for item in value if isinstance(item, int))


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None and str(value) else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _skills(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Requirement + preferred skills stored on the joined job row."""
    wanted: set[str] = set()
    for key in ("required_skills_json", "preferred_skills_json"):
        value = _json(row.get(key))
        if not isinstance(value, list):
            continue
        for skill in value:
            if isinstance(skill, str) and skill.strip():
                wanted.add(skill.strip().lower())
    return tuple(sorted(wanted))
