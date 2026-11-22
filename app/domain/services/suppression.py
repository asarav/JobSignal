"""Company-aware suppression of roles the user already applied to (spec §8.2).

Pure functions: the policy, the applied history and the incoming job go in; a
decision (or ``None``) comes out. Nothing here reads a clock or touches a
repository, so every rule is testable without a database.

Rules, in the order they are applied (spec §8.2):

1. Only applications with ``applied_at`` participate; ``interested``/``saved``
   never suppress anything. Callers pass only applied rows, and this module
   re-checks the timestamp rather than trusting the caller.
2. Exact repost of an applied job (same job, related job id, or canonical
   URL) is suppressed indefinitely while that chain exists.
3. A similar role at the same company is suppressed until
   ``applied_at + similar_role_cooldown_days``, provided the title family
   matches and the weighted similarity reaches the threshold.
4. A different role at the same company is allowed by default.
5. Company-wide suppression, when enabled, covers every role at the company
   until ``applied_at + company_wide_cooldown_days``.
6. Anything whose expiry has passed is not suppressed: the caller
   re-evaluates the job and it returns to normal views.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from app.domain.models.application import (
    AppliedJob,
    SuppressionCandidate,
    SuppressionDecision,
    SuppressionPolicy,
)
from app.domain.services.fingerprints import (
    jaccard_est,
    minhash_signature,
    normalize_text,
)

REASON_EXACT_REPOST = "exact_repost_of_applied"
REASON_SIMILAR_ROLE = "similar_role_cooldown"
REASON_COMPANY_WIDE = "company_wide_cooldown"

#: Spec §7.3 weights. ``salary`` is part of the model but not part of an
#: application snapshot, so it is renormalised away when unavailable.
SIMILARITY_WEIGHTS: Mapping[str, float] = {
    "title": 0.30,
    "location": 0.15,
    "description": 0.40,
    "department": 0.10,
    "salary": 0.05,
}


def _ratio(a: str, b: str) -> float:
    left, right = normalize_text(a), normalize_text(b)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _location_key(locations: Sequence[Mapping[str, object]]) -> set[str]:
    keys: set[str] = set()
    for entry in locations:
        if not isinstance(entry, Mapping):
            continue
        parts = [
            str(entry.get(field) or "").strip().lower()
            for field in ("city", "region", "country")
        ]
        raw = str(entry.get("raw") or "").strip().lower()
        key = "|".join(part for part in parts if part)
        if key:
            keys.add(key)
        elif raw:
            keys.add(raw)
    return keys


def _location_similarity(applied: AppliedJob, candidate: SuppressionCandidate) -> float | None:
    left, right = _location_key(applied.locations), _location_key(candidate.locations)
    if not left or not right:
        return None
    overlap = len(left & right) / len(left | right)
    same_workplace = applied.workplace_type == candidate.workplace_type
    return 0.8 * overlap + 0.2 * (1.0 if same_workplace else 0.0)


def similarity_breakdown(
    candidate: SuppressionCandidate, applied: AppliedJob
) -> dict[str, float]:
    """Per-dimension similarity plus the weighted ``score`` for two rows.

    Dimensions with no data on either side are skipped and the remaining
    weights renormalise, so a posting without a department is not punished for
    the missing field (spec §9.1 rule 2: missing is not negative).
    """
    dims: dict[str, float] = {"title": _ratio(candidate.title, applied.title)}

    location = _location_similarity(applied, candidate)
    if location is not None:
        dims["location"] = location

    description = jaccard_est(
        minhash_signature(candidate.description_text or ""),
        minhash_signature(applied.description_text or ""),
    )
    if candidate.description_text and applied.description_text:
        dims["description"] = description

    if candidate.department and applied.department:
        dims["department"] = _ratio(candidate.department, applied.department)

    total_weight = sum(SIMILARITY_WEIGHTS[name] for name in dims)
    score = 0.0
    if total_weight > 0:
        score = sum(SIMILARITY_WEIGHTS[name] * value for name, value in dims.items())
        score /= total_weight
    dims["score"] = round(score, 6)
    return dims


def _is_exact_repost(
    candidate: SuppressionCandidate, applied: AppliedJob
) -> tuple[bool, str]:
    if candidate.job_id == applied.job_id:
        return True, "same_job"
    if candidate.job_id in applied.related_job_ids:
        return True, "related_job"
    if applied.job_id in candidate.related_job_ids:
        return True, "related_job"
    left = (candidate.canonical_url or "").strip().rstrip("/").lower()
    right = (applied.canonical_url or "").strip().rstrip("/").lower()
    if left and right and left == right:
        return True, "same_canonical_url"
    return False, ""


def _expiry(applied_at: datetime, days: int) -> datetime:
    return applied_at + timedelta(days=max(0, days))


def _expired(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is not None and expires_at <= now


def _application_date(applied_at: datetime) -> str:
    return applied_at.date().isoformat()


def _exact_decision(
    candidate: SuppressionCandidate,
    applied: AppliedJob,
    *,
    evidence_kind: str,
    policy: SuppressionPolicy,
) -> SuppressionDecision:
    breakdown = similarity_breakdown(candidate, applied)
    return SuppressionDecision(
        reason=REASON_EXACT_REPOST,
        explanation=(
            f"You applied to “{applied.title}” on {_application_date(applied.applied_at)}."
            " This posting is the same job."
        ),
        similarity=float(breakdown["score"]),
        expires_at=None,
        evidence={
            "match": evidence_kind,
            "applied_title": applied.title,
            "applied_at": applied.applied_at.isoformat(),
            "similarity_breakdown": dict(breakdown),
        },
        application_id=applied.application_id,
        policy=policy.to_mapping(),
    )


def _similar_decision(
    candidate: SuppressionCandidate,
    applied: AppliedJob,
    *,
    policy: SuppressionPolicy,
    similarity: float,
    breakdown: Mapping[str, float],
    expires_at: datetime,
) -> SuppressionDecision:
    return SuppressionDecision(
        reason=REASON_SIMILAR_ROLE,
        explanation=(
            f"You applied to a similar role (“{applied.title}”) at this company on "
            f"{_application_date(applied.applied_at)}; it stays hidden until "
            f"{expires_at.date().isoformat()}."
        ),
        similarity=similarity,
        expires_at=expires_at,
        evidence={
            "applied_title": applied.title,
            "applied_at": applied.applied_at.isoformat(),
            "threshold": policy.similar_role_threshold,
            "cooldown_days": policy.similar_role_cooldown_days,
            "similarity_breakdown": dict(breakdown),
        },
        application_id=applied.application_id,
        policy=policy.to_mapping(),
    )


def _company_decision(
    applied: AppliedJob,
    *,
    policy: SuppressionPolicy,
    expires_at: datetime,
) -> SuppressionDecision:
    return SuppressionDecision(
        reason=REASON_COMPANY_WIDE,
        explanation=(
            f"You applied at this company on {_application_date(applied.applied_at)}; "
            f"all roles stay hidden until {expires_at.date().isoformat()}."
        ),
        similarity=0.0,
        expires_at=expires_at,
        evidence={
            "applied_title": applied.title,
            "applied_at": applied.applied_at.isoformat(),
            "cooldown_days": policy.company_wide_cooldown_days,
            "similarity_breakdown": {},
        },
        application_id=applied.application_id,
        policy=policy.to_mapping(),
    )


def evaluate_suppression(
    candidate: SuppressionCandidate,
    applications: Sequence[AppliedJob],
    policy: SuppressionPolicy,
    *,
    now: datetime,
) -> SuppressionDecision | None:
    """Decide whether ``candidate`` is hidden because of an earlier application.

    ``applications`` may contain rows for any company; only matches from the
    candidate's own company (or an exact repost link) can suppress it.
    """
    exact: SuppressionDecision | None = None
    best: SuppressionDecision | None = None
    block_same_company = policy.different_role_same_company == "block"

    for applied in applications:
        if applied.applied_at is None:
            # Rule 1: pre-application states never create a cooldown.
            continue

        is_repost, evidence_kind = _is_exact_repost(candidate, applied)
        if is_repost:
            if not policy.exact_repost_suppress:
                continue
            exact = _exact_decision(
                candidate, applied, evidence_kind=evidence_kind, policy=policy
            )
            break

        if applied.company_id != candidate.company_id:
            continue

        if policy.company_wide_cooldown_enabled or block_same_company:
            expires_at = _expiry(applied.applied_at, policy.company_wide_cooldown_days)
            if not _expired(expires_at, now):
                decision = _company_decision(applied, policy=policy, expires_at=expires_at)
                if best is None or decision.similarity > best.similarity:
                    best = decision

        if candidate.title_family and applied.title_family != candidate.title_family:
            # Rule 4: a different role at the same company is allowed.
            continue

        expires_at = _expiry(applied.applied_at, policy.similar_role_cooldown_days)
        if _expired(expires_at, now):
            continue
        breakdown = similarity_breakdown(candidate, applied)
        similarity = float(breakdown["score"])
        if similarity < policy.similar_role_threshold:
            continue
        decision = _similar_decision(
            candidate,
            applied,
            policy=policy,
            similarity=similarity,
            breakdown=breakdown,
            expires_at=expires_at,
        )
        if best is None or decision.similarity > best.similarity:
            best = decision

    # An exact repost outranks any cooldown: it is the same job, not a guess.
    return exact if exact is not None else best


def is_expired(record: Mapping[str, object], now: datetime) -> bool:
    """Whether a stored suppression row has passed its ``expires_at``."""
    raw = record.get("expires_at")
    if isinstance(raw, datetime):
        return raw <= now
    if isinstance(raw, str) and raw:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")) <= now
    return False
