"""Ranking policy: fit (satisfaction) and credibility (success evidence).

The two numbers stay separate in every output. Credibility is
``1 - risk``, and its influence is scaled by confidence so thin evidence barely
moves a posting. This module is pure so the policy matrix is unit-tested and the
UI never re-implements it (spec §9.8, §11).
"""

from __future__ import annotations

from dataclasses import dataclass

POLICIES: frozenset[str] = frozenset(
    {"show_all", "deprioritize", "hide_high_risk", "require_evidence"}
)

#: Default policy: a posting is moved down, never removed, and low-confidence
#: risk has proportionally less effect (spec §9.8).
DEFAULT_POLICY = "deprioritize"


def credibility_factor(risk: float | None, confidence: float | None) -> float:
    """How much of a fit score survives the credibility policy."""
    if risk is None:
        return 1.0
    conf = float(confidence or 0.0)
    return max(0.0, 1.0 - 0.5 * float(risk) * min(1.0, conf / 0.5))


def rank_score(
    fit: float | None,
    risk: float | None,
    confidence: float | None,
    *,
    policy: str = DEFAULT_POLICY,
) -> float | None:
    """``fit × credibility_factor`` under ``deprioritize``; ``fit`` otherwise."""
    if fit is None:
        return None
    if policy in {"show_all", "hide_high_risk", "require_evidence"}:
        # Hiding policies filter; they do not also reorder.
        return float(fit)
    return float(fit) * credibility_factor(risk, confidence)


def visibility(
    risk_class: str | None,
    confidence: float | None,
    *,
    policy: str = DEFAULT_POLICY,
) -> str:
    """``shown`` or ``hidden`` under the profile's credibility policy."""
    if policy not in POLICIES:
        policy = DEFAULT_POLICY
    if risk_class is None or policy in {"show_all", "deprioritize"}:
        return "shown"
    conf = float(confidence or 0.0)
    if policy == "hide_high_risk":
        if risk_class == "likely_inactive" and conf >= 0.5:
            return "hidden"
        return "shown"
    if policy == "require_evidence":
        return "shown" if risk_class == "likely_active" else "hidden"
    return "shown"


@dataclass(frozen=True, slots=True)
class RankExplanation:
    rank_score: float | None
    reasons: tuple[str, ...]


def explain(
    *,
    fit: float | None,
    risk: float | None,
    confidence: float | None,
    risk_label: str | None,
    policy: str,
    visibility_state: str,
) -> RankExplanation:
    reasons: list[str] = []
    if fit is None:
        reasons.append(
            "No fit score yet for this profile, so this row is ordered by recency."
        )
    else:
        reasons.append(f"Fit {fit:.2f} (heuristic), weighted by your profile.")
    if risk is None:
        reasons.append("Not assessed for posting risk yet.")
    else:
        evidence = f"confidence {float(confidence or 0.0):.2f}"
        label = risk_label or "posting risk"
        reasons.append(f"{label}: risk {risk:.2f}, {evidence}.")
        if policy == "deprioritize" and visibility_state == "shown":
            factor = credibility_factor(risk, confidence)
            if factor < 1.0:
                reasons.append(
                    f"Moved down by the credibility policy (factor {factor:.2f}); "
                    "it is not hidden."
                )
    if visibility_state == "hidden":
        reasons.append("Hidden by the active credibility policy; reachable with the toggle.")
    return RankExplanation(
        rank_score=rank_score(fit, risk, confidence, policy=policy),
        reasons=tuple(reasons),
    )


__all__ = [
    "DEFAULT_POLICY",
    "POLICIES",
    "RankExplanation",
    "credibility_factor",
    "explain",
    "rank_score",
    "visibility",
]
