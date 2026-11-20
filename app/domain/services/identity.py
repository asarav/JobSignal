"""Job identity resolution (spec §7.3).

Pure domain service: takes the incoming posting's fingerprints plus a list of
candidate existing postings and returns an :class:`IdentityDecision`. No I/O.
"""

from __future__ import annotations

import difflib
from collections.abc import Sequence

from app.domain.models.identity import IdentityDecision, JobFingerprintSet, SimilarityEvidence

_DEFAULT_WEIGHTS = {
    "title": 0.30,
    "location": 0.15,
    "description": 0.40,
    "department": 0.10,
    "salary": 0.05,
}


def _title_sim(a: JobFingerprintSet, b: JobFingerprintSet) -> float:
    if a.title_key == b.title_key:
        return 1.0
    if a.title_family and a.title_family == b.title_family:
        return 0.85
    # fuzzy fallback on title_key
    return difflib.SequenceMatcher(None, a.title_key, b.title_key).ratio()


def _location_sim(a: JobFingerprintSet, b: JobFingerprintSet) -> float:
    if a.location_key is None or b.location_key is None:
        return 0.5
    return 1.0 if a.location_key == b.location_key else 0.0


def _desc_sim(a: JobFingerprintSet, b: JobFingerprintSet) -> float:
    from app.domain.services.fingerprints import hamming, jaccard_est

    candidates = []
    if a.desc_minhash and b.desc_minhash:
        candidates.append(jaccard_est(a.desc_minhash, b.desc_minhash))
    if a.desc_simhash64 is not None and b.desc_simhash64 is not None:
        candidates.append(max(0.0, 1.0 - hamming(a.desc_simhash64, b.desc_simhash64) / 64.0))
    if a.desc_exact_hash == b.desc_exact_hash:
        candidates.append(1.0)
    return max(candidates) if candidates else 0.0


def _dept_sim(a: JobFingerprintSet, b: JobFingerprintSet) -> float:
    if a.department is None and b.department is None:
        return 0.5
    if a.department is not None and a.department == b.department:
        return 1.0
    if a.department is None or b.department is None:
        return 0.5
    return 0.0


def _salary_sim(a: JobFingerprintSet, b: JobFingerprintSet) -> float:
    if a.salary_min is None or b.salary_min is None:
        return 0.5
    if a.salary_min == b.salary_min:
        return 1.0
    ratio = min(a.salary_min, b.salary_min) / max(a.salary_min, b.salary_min)
    if ratio >= 0.9:
        return 0.8
    return 0.3


def compute_similarity(
    a: JobFingerprintSet, b: JobFingerprintSet, weights: dict[str, float] | None = None
) -> SimilarityEvidence:
    w = weights or _DEFAULT_WEIGHTS
    title_sim = _title_sim(a, b)
    location_sim = _location_sim(a, b)
    desc_sim = _desc_sim(a, b)
    dept_sim = _dept_sim(a, b)
    salary_sim = _salary_sim(a, b)
    score = (
        w.get("title", 0.30) * title_sim
        + w.get("location", 0.15) * location_sim
        + w.get("description", 0.40) * desc_sim
        + w.get("department", 0.10) * dept_sim
        + w.get("salary", 0.05) * salary_sim
    )
    # normalize by sum of weights
    total_w = sum(
        w.get(k, 0.0) for k in ("title", "location", "description", "department", "salary")
    )
    if total_w > 0:
        score = score / total_w
    reasons = []
    if title_sim >= 0.9:
        reasons.append("title_match")
    if location_sim >= 0.8:
        reasons.append("location_match")
    if desc_sim >= 0.9:
        reasons.append("desc_match")
    return SimilarityEvidence(
        score=score,
        title_sim=title_sim,
        location_sim=location_sim,
        desc_sim=desc_sim,
        dept_sim=dept_sim,
        salary_sim=salary_sim,
        reasons=tuple(reasons),
    )


def _has_different_req_ids(a: JobFingerprintSet, b: JobFingerprintSet) -> bool:
    if not a.req_ids or not b.req_ids:
        return False
    return not (set(a.req_ids) & set(b.req_ids))


def resolve(
    incoming: JobFingerprintSet,
    candidates: Sequence[JobFingerprintSet],
    *,
    thresholds: dict[str, float] | None = None,
    material_change_delta: float = 0.10,
) -> IdentityDecision:
    thr = thresholds or {
        "reposted_identical": 0.90,
        "materially_updated": 0.75,
        "similar_distinct": 0.60,
    }
    # 1. Exact alias hit.
    for c in candidates:
        if incoming.ats_key and incoming.ats_key == c.ats_key:
            if incoming.desc_exact_hash == c.desc_exact_hash:
                return IdentityDecision(
                    action="attach_alias",
                    job_id=None,
                    repost_class="exact_duplicate",
                    confidence=1.0,
                    evidence=("exact alias hit, identical content",),
                )
            # differs
            material = _is_material(incoming, c, material_change_delta)
            return IdentityDecision(
                action="new_snapshot",
                job_id=None,
                repost_class="materially_updated" if material else "reposted_identical",
                confidence=0.9,
                evidence=("exact alias hit, content differs",),
                material=material,
            )
        if incoming.url_key and incoming.url_key == c.url_key:
            if incoming.desc_exact_hash == c.desc_exact_hash:
                return IdentityDecision(
                    action="attach_alias",
                    job_id=None,
                    repost_class="exact_duplicate",
                    confidence=1.0,
                    evidence=("url alias hit, identical content",),
                )
            material = _is_material(incoming, c, material_change_delta)
            return IdentityDecision(
                action="new_snapshot",
                job_id=None,
                repost_class="materially_updated" if material else "reposted_identical",
                confidence=0.9,
                evidence=("url alias hit, content differs",),
                material=material,
            )

    # 2. Candidate retrieval (blocking): same company, title match.
    blocking_candidates: list[JobFingerprintSet] = []
    for c in candidates:
        if incoming.company_id is None or c.company_id != incoming.company_id:
            continue
        title_match = incoming.title_key == c.title_key or (
            incoming.title_family is not None and incoming.title_family == c.title_family
        )
        if title_match:
            blocking_candidates.append(c)

    if not blocking_candidates:
        return IdentityDecision(
            action="new_job",
            job_id=None,
            repost_class="new_position",
            confidence=1.0,
            evidence=("no blocking candidates",),
        )

    # 3. Pairwise similarity.
    scored: list[tuple[JobFingerprintSet, SimilarityEvidence]] = []
    for c in blocking_candidates:
        sim = compute_similarity(incoming, c)
        scored.append((c, sim))

    # guards: different location (non-remote) caps at similar_distinct.
    # different req ids → never merge unless one absent.
    best = max(scored, key=lambda x: x[1].score)
    # check tie
    if len(scored) >= 2:
        sorted_scores = sorted(scored, key=lambda x: x[1].score, reverse=True)
        if sorted_scores[0][1].score - sorted_scores[1][1].score < 0.03:
            # ambiguous tie → similar_distinct
            return IdentityDecision(
                action="new_job",
                job_id=None,
                repost_class="similar_distinct",
                confidence=best[1].score,
                evidence=("ambiguous tie between top candidates",),
                ambiguous=True,
                related_job_ids=tuple(c.url_key for c, _ in [best]),
            )

    c, sim = best
    title_sim = sim.title_sim
    location_sim = sim.location_sim
    desc_sim = sim.desc_sim

    # guards
    over_merge_cap = False
    if (
        incoming.location_key is not None
        and c.location_key is not None
        and incoming.location_key != c.location_key
    ):
        # non-remote different location
        if "remote" not in (incoming.location_key or "") and "remote" not in (c.location_key or ""):
            over_merge_cap = True
    if _has_different_req_ids(incoming, c):
        over_merge_cap = True
    if (
        incoming.department is not None
        and c.department is not None
        and incoming.department != c.department
    ):
        over_merge_cap = True

    # decision table
    reposted_identical_thr = thr.get("reposted_identical", 0.90)
    materially_updated_thr = thr.get("materially_updated", 0.75)
    similar_distinct_thr = thr.get("similar_distinct", 0.60)

    if incoming.desc_exact_hash == c.desc_exact_hash and title_sim >= 0.95 and location_sim >= 0.8:
        repost_class = "reposted_identical"
    elif (
        sim.score >= reposted_identical_thr
        and title_sim >= 0.9
        and location_sim >= 0.8
        and desc_sim >= 0.90
    ):
        repost_class = "reposted_identical"
    elif (
        reposted_identical_thr > sim.score >= materially_updated_thr
        and title_sim >= 0.9
        and location_sim >= 0.8
    ):
        repost_class = "materially_updated"
    elif similar_distinct_thr <= sim.score < materially_updated_thr:
        repost_class = "similar_distinct"
    elif (
        incoming.title_family is not None
        and incoming.title_family == c.title_family
        and (incoming.location_key != c.location_key)
    ):
        repost_class = "similar_distinct"
    else:
        repost_class = "new_position"

    if over_merge_cap and repost_class in ("reposted_identical", "materially_updated"):
        repost_class = "similar_distinct"

    if repost_class == "new_position":
        return IdentityDecision(
            action="new_job",
            job_id=None,
            repost_class="new_position",
            confidence=sim.score,
            evidence=tuple(sim.reasons),
        )

    if repost_class == "similar_distinct":
        return IdentityDecision(
            action="new_job",
            job_id=None,
            repost_class="similar_distinct",
            confidence=sim.score,
            evidence=tuple(sim.reasons),
            related_job_ids=(c.url_key,),
        )

    # reposted_identical or materially_updated → attach alias
    material = repost_class == "materially_updated" or _is_material(
        incoming, c, material_change_delta
    )
    action = "attach_alias" if repost_class == "reposted_identical" else "new_snapshot"
    return IdentityDecision(
        action=action,
        job_id=None,
        repost_class=repost_class,
        confidence=sim.score,
        evidence=tuple(sim.reasons),
        material=material,
        related_job_ids=(c.url_key,),
    )


def _is_material(incoming: JobFingerprintSet, existing: JobFingerprintSet, delta: float) -> bool:
    # title/location/salary/seniority change
    if incoming.title_key != existing.title_key:
        return True
    if incoming.location_key != existing.location_key:
        return True
    if incoming.salary_min != existing.salary_min:
        return True
    # description jaccard delta
    from app.domain.services.fingerprints import jaccard_est

    if incoming.desc_minhash and existing.desc_minhash:
        j = jaccard_est(incoming.desc_minhash, existing.desc_minhash)
        if (1.0 - j) >= delta:
            return True
    if incoming.desc_exact_hash != existing.desc_exact_hash:
        # if hashes differ at all, could be material; use delta on simhash
        if incoming.desc_simhash64 is not None and existing.desc_simhash64 is not None:
            from app.domain.services.fingerprints import hamming

            sim = 1.0 - hamming(incoming.desc_simhash64, existing.desc_simhash64) / 64.0
            if (1.0 - sim) >= delta:
                return True
    return False
