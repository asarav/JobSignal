"""Relevance tiers for discovery sources (M6).

The discovery funnel spends the same crawl budget differently depending on how
plausibly a source can produce a posting the candidate could get.  Tiers are a
pure function of the stored postings, the candidate profile and the hard
filters of the user's search profiles, so the assignment is reproducible and
its reason can be shown to the user.

* ``hot``  - a stored posting passes every hard filter *and* matches both the
  candidate's target titles and at least one of their skills.
* ``warm`` - a stored posting passes the hard filters and matches a title or a
  skill.
* ``cold`` - everything else, including boards with no postings stored yet.

The crawler maps the tier to an interval (``DEFAULT_TIER_INTERVALS_S``); a tier
never causes a source to be crawled more often than its interval.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.domain.services.hard_filters import apply_hard_filters

TIER_HOT = "hot"
TIER_WARM = "warm"
TIER_COLD = "cold"
TIERS: tuple[str, ...] = (TIER_HOT, TIER_WARM, TIER_COLD)

#: Crawl cadence per tier: hot (hourly check, effectively every pass), warm
#: (daily), cold (weekly).  Configurable through ``discovery.tier_intervals_s``.
DEFAULT_TIER_INTERVALS_S: tuple[float, float, float] = (
    3600.0,
    24 * 3600.0,
    7 * 24 * 3600.0,
)

_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")


@dataclass(frozen=True, slots=True)
class TierDecision:
    """A tier and the user-facing reason for it."""

    tier: str
    reason: str


def assign_tier(
    jobs: Sequence[Mapping[str, object]],
    *,
    skills: Sequence[str],
    titles: Sequence[str],
    filter_sets: Sequence[Mapping[str, object]] = (),
) -> TierDecision:
    """Rank one source from its stored postings.

    ``filter_sets`` are the hard filters of the user's search profiles; a
    posting qualifies when it passes *any* of them.  With no non-empty filter
    set every posting qualifies, which is how a profile with only skills and
    target titles still produces useful tiers.
    """
    active = [
        job
        for job in jobs
        if str(job.get("posting_status") or "active") == "active"
    ]
    skill_tokens = _tokens(skills)
    title_terms = [title.strip().lower() for title in titles if title.strip()]
    sets = [filters for filters in filter_sets if filters] or [{}]

    best: TierDecision | None = None
    ranked = {TIER_COLD: 0, TIER_WARM: 1, TIER_HOT: 2}
    for filters in sets:
        for job in apply_hard_filters(active, filters):
            decision = _rank_job(job, skill_tokens, title_terms)
            if best is None or ranked[decision.tier] > ranked[best.tier]:
                best = decision

    if best is not None and best.tier != TIER_COLD:
        return best
    if not active:
        return TierDecision(
            tier=TIER_COLD,
            reason=(
                "no postings stored yet; keeping the slow cadence until a crawl "
                "or the index confirms relevance"
            ),
        )
    if best is None:
        return TierDecision(
            tier=TIER_COLD,
            reason="no stored posting passes your hard filters",
        )
    return best


def interval_for_tier(
    tier: str, intervals: Sequence[float] = DEFAULT_TIER_INTERVALS_S
) -> float:
    """The crawl interval the tier allows; unknown tiers fall back to cold."""
    index = TIERS.index(tier) if tier in TIERS else TIERS.index(TIER_COLD)
    values = _intervals(intervals)
    return values[index]


def _rank_job(
    job: Mapping[str, object],
    skill_tokens: set[str],
    title_terms: Sequence[str],
) -> TierDecision:
    title = str(job.get("title") or "").strip()
    text = f"{title} {job.get('description_text') or ''}".lower()
    matched = sorted(
        skill for skill in skill_tokens if re.search(rf"(?<!\w){re.escape(skill)}(?!\w)", text)
    )
    title_match = any(term in title.lower() for term in title_terms)
    label = f"'{title}'" if title else "a stored posting"

    if title_match and matched:
        return TierDecision(
            tier=TIER_HOT,
            reason=f"{label} matches your target titles and skills ({', '.join(matched[:3])})",
        )
    if title_match:
        return TierDecision(
            tier=TIER_WARM,
            reason=f"{label} matches your target titles",
        )
    if matched:
        return TierDecision(
            tier=TIER_WARM,
            reason=f"{label} matches your skills ({', '.join(matched[:3])})",
        )
    return TierDecision(
        tier=TIER_COLD,
        reason=f"{label} passes your hard filters but matches none of your titles or skills",
    )


def _tokens(values: Sequence[str]) -> set[str]:
    tokens: set[str] = set()
    for value in values:
        tokens.update(_TOKEN_RE.findall(str(value).lower()))
    return tokens


def _intervals(intervals: Sequence[float]) -> tuple[float, float, float]:
    if len(intervals) != 3:
        return DEFAULT_TIER_INTERVALS_S
    values = []
    for value in intervals:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return DEFAULT_TIER_INTERVALS_S
        values.append(float(value))
    return values[0], values[1], values[2]
