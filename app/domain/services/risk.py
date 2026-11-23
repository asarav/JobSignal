"""``risk.heuristic_v1``: evidence-weighted risk, computed separately from confidence.

The model is a pure function of the signal list and the configuration (spec
§9.4). It never discards a posting, never claims certainty, and never lets weak
evidence push a posting past "needs verification": the prior shrinks thin
evidence and only a strong signal can lift a verdict on its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from app.domain.models.common import Explanation, Reason
from app.domain.models.enums import RiskClass, SignalStatus
from app.domain.models.posting import (
    RiskAssessment,
    RiskModelConfig,
    RiskThresholds,
    SignalResult,
)

MODEL_ID = "risk.heuristic_v1"
MODEL_VERSION = "1.0.0"

#: Cautious, user-facing class labels (spec §9.5). No accusations, no certainty.
CLASS_LABELS: dict[RiskClass, str] = {
    RiskClass.LIKELY_ACTIVE: "No strong sign of inactivity",
    RiskClass.NEEDS_VERIFICATION: "Needs verification",
    RiskClass.LIKELY_STALE: "Possibly stale posting",
    RiskClass.LIKELY_INACTIVE: "Likely inactive / potential ghost job",
}


def classify(
    risk: float,
    confidence: float,
    thresholds: RiskThresholds | None = None,
) -> RiskClass:
    """Classify a risk/confidence pair with the documented thresholds (§9.5)."""
    t = thresholds or RiskThresholds()
    if risk >= t.inactive_risk_min and confidence >= t.inactive_confidence_min:
        return RiskClass.LIKELY_INACTIVE
    if t.stale_risk_min <= risk < t.inactive_risk_min and confidence >= (
        t.stale_confidence_min
    ):
        return RiskClass.LIKELY_STALE
    if (
        risk >= t.stale_alt_risk_min
        and t.stale_alt_confidence_min <= confidence < t.inactive_confidence_min
    ):
        return RiskClass.LIKELY_STALE
    if risk < t.active_risk_max and confidence >= t.active_confidence_min:
        return RiskClass.LIKELY_ACTIVE
    return RiskClass.NEEDS_VERIFICATION


def sufficiency(coverage: float, thresholds: RiskThresholds | None = None) -> str:
    t = thresholds or RiskThresholds()
    if coverage <= 0 or coverage < t.limited_coverage:
        return "insufficient"
    if coverage < t.sufficient_coverage:
        return "limited"
    return "sufficient"


class HeuristicRiskModel:
    """The ``GhostJobRiskModel`` implementation registered by the container."""

    id = MODEL_ID
    version = MODEL_VERSION

    def assess(
        self,
        signals: Sequence[SignalResult],
        cfg: RiskModelConfig,
        now: datetime,
    ) -> RiskAssessment:
        weights = {signal.signal_id: cfg.weight_for(signal.signal_id) for signal in signals}
        available = [s for s in signals if s.status is SignalStatus.AVAILABLE]
        available = [s for s in available if s.risk_contribution is not None]

        total_weight = sum(weights.values())
        available_weight = sum(weights[s.signal_id] for s in available)
        # Confidence-weighted mass, exactly as §9.4 defines M.
        mass = sum(weights[s.signal_id] * s.confidence for s in available)
        evidence_mass = sum(
            weights[s.signal_id] * s.confidence
            * float(s.risk_contribution or 0.0)
            for s in available
        )
        prior_mass = cfg.prior_weight * cfg.prior_risk

        # 1) Evidence-weighted risk with a weak prior.
        denominator = cfg.prior_weight + mass
        risk = (
            (prior_mass + evidence_mass) / denominator
            if denominator
            else cfg.prior_risk
        )

        # 2) A strong, explicit signal sets a floor rather than an average.
        strong = [
            s for s in available if s.strong and s.risk_contribution is not None
        ]
        if strong:
            floor = max(float(s.risk_contribution or 0.0) for s in strong) * 0.95
            risk = max(risk, floor)

        # 3) Confidence is coverage and quality, never the risk number again.
        coverage = available_weight / total_weight if total_weight else 0.0
        quality = (
            sum(weights[s.signal_id] * s.confidence for s in available) / available_weight
            if available_weight
            else 0.0
        )
        confidence = max(0.0, min(1.0, 0.5 * coverage + 0.5 * quality))
        if strong:
            confidence = max(confidence, 0.70)
        if available_weight == 0:
            confidence = 0.0

        # 4) Contradictory evidence lowers confidence, not risk.
        confident = [s for s in available if s.confidence >= 0.5]
        if len(confident) >= 2:
            contributions = [float(s.risk_contribution or 0.0) for s in confident]
            if max(contributions) - min(contributions) >= 0.7:
                confidence *= 0.85

        risk_class = classify(risk, confidence, cfg.thresholds)
        top = sorted(
            available,
            key=lambda s: weights[s.signal_id]
            * s.confidence
            * abs(float(s.risk_contribution or 0.0) - cfg.prior_risk),
            reverse=True,
        )[:3]

        reasons = [
            Reason(
                code=s.signal_id,
                message=s.explanation,
                weight=round(
                    weights[s.signal_id] * s.confidence * float(s.risk_contribution or 0.0),
                    4,
                ),
                evidence=tuple(s.evidence),
            )
            for s in top
        ]
        if not available:
            reasons.append(
                Reason(
                    code="risk.insufficient_evidence",
                    message=(
                        "No signal provider had usable evidence, so only the prior "
                        "applies."
                    ),
                    weight=None,
                )
            )
        summary = (
            f"{CLASS_LABELS[risk_class]} (evidence: {sufficiency(coverage, cfg.thresholds)})"
        )
        return RiskAssessment(
            model_id=cfg.model_id,
            model_version=cfg.model_version,
            risk=round(risk, 4),
            confidence=round(max(0.0, min(1.0, confidence)), 4),
            risk_class=risk_class,
            explanation=Explanation(summary=summary, reasons=tuple(reasons)),
            signals=tuple(signals),
            evaluated_at=now,
            evidence_sufficiency=sufficiency(coverage, cfg.thresholds),
            config_hash=cfg.fingerprint(),
            top_signals=tuple(s.signal_id for s in top),
        )
