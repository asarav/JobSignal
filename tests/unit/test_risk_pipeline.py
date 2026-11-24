"""The §19.7 cases not already covered by the pure engine suite.

These pin the aggregation behaviour the pipeline depends on: weak evidence can
never lift a posting past "needs verification", a strong signal sets a floor,
and endpoint checks distinguish our failures from the posting's.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.application.intelligence import wilson_interval
from app.domain.models.enums import RiskClass, SignalStatus
from app.domain.models.posting import (
    EndpointPoint,
    JobHistory,
    RiskModelConfig,
    SignalContext,
    SignalResult,
)
from app.domain.services.risk import HeuristicRiskModel, classify
from app.domain.services.signals import EndpointAvailabilitySignal

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _signal(signal_id: str, risk: float, confidence: float) -> SignalResult:
    return SignalResult(
        signal_id=signal_id,
        provider_version="1.0.0",
        status=SignalStatus.AVAILABLE,
        risk_contribution=risk,
        confidence=confidence,
        explanation="weak evidence",
        observed_at=NOW,
        evaluated_at=NOW,
    )


def _history(**kwargs) -> JobHistory:
    return JobHistory(job_id="job_1", **kwargs)


def _endpoint_history(*states: str) -> JobHistory:
    return _history(
        first_seen_at=NOW - timedelta(days=30),
        last_seen_at=NOW,
        endpoints=tuple(
            EndpointPoint(
                at=NOW - timedelta(days=offset),
                state=state,
                http_status=200,
            )
            for offset, state in enumerate(states)
        ),
    )


def test_three_weak_signals_cannot_reach_likely_inactive() -> None:
    signals = [
        _signal("signal.repost_frequency", 0.5, 0.3),
        _signal("signal.endpoint_availability", 0.5, 0.3),
        _signal("signal.company_hiring_activity", 0.5, 0.3),
    ]

    assessment = HeuristicRiskModel().assess(
        signals, RiskModelConfig("risk.heuristic_v1", "1.0.0"), NOW
    )

    assert assessment.risk_class is not RiskClass.LIKELY_INACTIVE
    assert assessment.risk < 0.5


def test_a_single_timeout_is_missing_evidence() -> None:
    result = EndpointAvailabilitySignal().evaluate(
        _endpoint_history("timeout"),
        SignalContext(job_id="job_1", now=NOW),
    )

    assert result.status is SignalStatus.MISSING
    assert result.risk_contribution is None


def test_two_consecutive_timeouts_are_weak_evidence() -> None:
    result = EndpointAvailabilitySignal().evaluate(
        _endpoint_history("timeout", "timeout"),
        SignalContext(job_id="job_1", now=NOW),
    )

    assert result.status is SignalStatus.AVAILABLE
    assert result.risk_contribution == pytest.approx(0.40)
    assert result.strong is False


def test_a_success_after_failures_is_active_evidence() -> None:
    # Newest first argument: the page answered normally on the latest check.
    result = EndpointAvailabilitySignal().evaluate(
        _endpoint_history("ok", "timeout"),
        SignalContext(job_id="job_1", now=NOW),
    )

    assert result.status is SignalStatus.AVAILABLE
    assert result.risk_contribution == pytest.approx(0.0)


@pytest.mark.parametrize("state", ["blocked", "auth_required"])
def test_blocks_and_login_walls_are_never_negative_evidence(state: str) -> None:
    result = EndpointAvailabilitySignal().evaluate(
        _endpoint_history(state),
        SignalContext(job_id="job_1", now=NOW),
    )

    assert result.status is SignalStatus.MISSING
    assert result.risk_contribution is None


@pytest.mark.parametrize(
    ("risk", "confidence", "expected"),
    [
        (0.29, 0.35, RiskClass.LIKELY_ACTIVE),
        (0.30, 0.35, RiskClass.NEEDS_VERIFICATION),
        (0.50, 0.40, RiskClass.LIKELY_STALE),
        (0.50, 0.39, RiskClass.NEEDS_VERIFICATION),
        (0.74, 0.90, RiskClass.LIKELY_STALE),
        (0.75, 0.50, RiskClass.LIKELY_INACTIVE),
        (0.75, 0.49, RiskClass.LIKELY_STALE),
        (0.95, 0.20, RiskClass.NEEDS_VERIFICATION),
    ],
)
def test_classification_boundaries(
    risk: float, confidence: float, expected: RiskClass
) -> None:
    assert classify(risk, confidence) is expected


def test_wilson_interval_stays_inside_the_unit_range() -> None:
    assert wilson_interval(0, 0) == (0.0, 0.0)
    low, high = wilson_interval(0, 5)
    assert low == 0.0
    assert 0.0 < high < 1.0
    low, high = wilson_interval(5, 5)
    assert 0.0 < low < 1.0
    assert high == 1.0
