"""``risk.heuristic_v1`` and the signal providers (spec §9.3–9.6, §19.7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.domain.models.enums import RiskClass, SignalStatus
from app.domain.models.posting import (
    CohortStats,
    CompanySummaryView,
    EndpointPoint,
    JobHistory,
    RelatedJob,
    RepostPoint,
    RiskModelConfig,
    SignalContext,
    SignalResult,
    SnapshotPoint,
)
from app.domain.services.risk import (
    MODEL_ID,
    MODEL_VERSION,
    HeuristicRiskModel,
    classify,
)
from app.domain.services.signals import (
    DEFAULT_SIGNAL_PROVIDERS,
    CompanyHiringActivitySignal,
    ContentStalenessSignal,
    DuplicateConsistencySignal,
    EndpointAvailabilitySignal,
    ExplicitClosureSignal,
    ImplausibleRequirementsSignal,
    PostingAgeSignal,
    RepostFrequencySignal,
)

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
DEFAULT_WEIGHTS = {
    "posting_age": 0.10,
    "repost_frequency": 0.20,
    "endpoint_availability": 0.25,
    "content_staleness": 0.10,
    "duplicate_consistency": 0.10,
    "company_hiring_activity": 0.15,
    "explicit_closure_language": 0.30,
    "implausible_requirements": 0.05,
}
CFG = RiskModelConfig(
    model_id=MODEL_ID, model_version=MODEL_VERSION, weights=DEFAULT_WEIGHTS
)
CTX = SignalContext(job_id="job_1", now=NOW)
MODEL = HeuristicRiskModel()


def _available(
    signal_id: str, risk: float, confidence: float, *, strong: bool = False
) -> SignalResult:
    return SignalResult(
        signal_id=signal_id,
        provider_version="test",
        status=SignalStatus.AVAILABLE,
        risk_contribution=risk,
        confidence=confidence,
        strong=strong,
        explanation=f"{signal_id} test result",
    )


def _missing(signal_id: str) -> SignalResult:
    return SignalResult(
        signal_id=signal_id,
        provider_version="test",
        status=SignalStatus.MISSING,
        risk_contribution=None,
        confidence=0.0,
        missing_reason="no data in this test",
        explanation="Not assessed: no data in this test.",
    )


def _signals(**available: SignalResult) -> list[SignalResult]:
    """The full registered set, so W_all is the real 1.25 (§9.4)."""
    return [
        available.get(provider.id.removeprefix("signal.")) or _missing(provider.id)
        for provider in DEFAULT_SIGNAL_PROVIDERS
    ]


def _history(**kwargs: object) -> JobHistory:
    defaults: dict[str, object] = {
        "job_id": "job_1",
        "first_seen_at": NOW - timedelta(days=30),
        "last_seen_at": NOW,
    }
    defaults.update(kwargs)
    return JobHistory(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------- aggregation


def test_only_a_posting_age_signal_stays_needs_verification() -> None:
    """Worked example 1 (§9.4): thin evidence must not read as a ghost job."""
    signals = _signals(
        posting_age=_available("signal.posting_age", 0.5, 0.40)
    )
    assessment = MODEL.assess(signals, CFG, NOW)
    assert assessment.risk == pytest.approx(0.263, abs=0.005)
    assert assessment.confidence == pytest.approx(0.24, abs=0.005)
    assert assessment.risk_class is RiskClass.NEEDS_VERIFICATION
    assert assessment.evidence_sufficiency == "insufficient"


def test_an_explicit_closure_is_strong_evidence() -> None:
    """Worked example 2 (§9.4): a strong signal sets a floor and a confidence floor."""
    signals = _signals(
        explicit_closure_language=_available(
            "signal.explicit_closure_language", 0.95, 0.90, strong=True
        )
    )
    assessment = MODEL.assess(signals, CFG, NOW)
    assert assessment.risk == pytest.approx(0.9025, abs=0.001)
    assert assessment.confidence >= 0.70
    assert assessment.risk_class is RiskClass.LIKELY_INACTIVE


def test_no_available_signals_yields_the_prior_and_zero_confidence() -> None:
    """Worked example 3 (§9.4)."""
    assessment = MODEL.assess(_signals(), CFG, NOW)
    assert assessment.risk == pytest.approx(0.20)
    assert assessment.confidence == 0.0
    assert assessment.risk_class is RiskClass.NEEDS_VERIFICATION
    assert assessment.evidence_sufficiency == "insufficient"
    assert assessment.explanation.reasons[0].code == "risk.insufficient_evidence"


def test_three_weak_signals_do_not_reach_likely_inactive() -> None:
    weak = 0.5
    signals = _signals(
        posting_age=_available("signal.posting_age", weak, 0.3),
        content_staleness=_available("signal.content_staleness", weak, 0.3),
        duplicate_consistency=_available("signal.duplicate_consistency", weak, 0.3),
    )
    assessment = MODEL.assess(signals, CFG, NOW)
    assert assessment.risk < 0.5
    assert assessment.risk_class is RiskClass.NEEDS_VERIFICATION


def test_missing_evidence_is_not_treated_as_healthy() -> None:
    """A blocked endpoint must not look cleaner than a checked, open one."""
    ok = _signals(
        endpoint_availability=_available("signal.endpoint_availability", 0.0, 0.60)
    )
    blocked = _signals()
    assert MODEL.assess(blocked, CFG, NOW).risk >= MODEL.assess(ok, CFG, NOW).risk


def test_a_missing_signal_never_adds_evidence_mass() -> None:
    base = _signals(
        posting_age=_available("signal.posting_age", 0.5, 0.4),
    )
    with_missing_last_change = _signals(
        posting_age=_available("signal.posting_age", 0.5, 0.4),
        content_staleness=_missing("signal.content_staleness"),
    )
    a = MODEL.assess(base, CFG, NOW)
    b = MODEL.assess(with_missing_last_change, CFG, NOW)
    assert a.risk == b.risk
    assert a.confidence == b.confidence


def test_contradictory_signals_lower_confidence_not_risk() -> None:
    signals = _signals(
        endpoint_availability=_available("signal.endpoint_availability", 0.0, 0.60),
        explicit_closure_language=_available(
            "signal.explicit_closure_language", 0.95, 0.90, strong=True
        ),
    )
    assessment = MODEL.assess(signals, CFG, NOW)
    assert assessment.risk >= 0.90
    assert assessment.risk_class is RiskClass.LIKELY_INACTIVE


@pytest.mark.parametrize(
    ("risk", "confidence", "expected"),
    [
        (0.90, 0.70, RiskClass.LIKELY_INACTIVE),
        (0.75, 0.50, RiskClass.LIKELY_INACTIVE),
        (0.75, 0.49, RiskClass.LIKELY_STALE),
        (0.75, 0.30, RiskClass.LIKELY_STALE),
        (0.75, 0.29, RiskClass.NEEDS_VERIFICATION),
        (0.74, 0.50, RiskClass.LIKELY_STALE),
        (0.74, 0.39, RiskClass.NEEDS_VERIFICATION),
        (0.50, 0.40, RiskClass.LIKELY_STALE),
        (0.50, 0.39, RiskClass.NEEDS_VERIFICATION),
        (0.30, 0.60, RiskClass.NEEDS_VERIFICATION),
        (0.29, 0.35, RiskClass.LIKELY_ACTIVE),
        (0.29, 0.34, RiskClass.NEEDS_VERIFICATION),
    ],
)
def test_classification_boundaries(
    risk: float, confidence: float, expected: RiskClass
) -> None:
    assert classify(risk, confidence) is expected


def test_assessment_is_reproducible_from_stored_signals() -> None:
    signals = _signals(
        posting_age=_available("signal.posting_age", 0.4, 0.5),
        endpoint_availability=_available("signal.endpoint_availability", 0.7, 0.6),
    )
    first = MODEL.assess(signals, CFG, NOW)
    second = MODEL.assess(signals, CFG, NOW + timedelta(days=1))
    assert (first.risk, first.confidence, first.risk_class) == (
        second.risk,
        second.confidence,
        second.risk_class,
    )
    assert first.config_hash == second.config_hash
    assert first.model_version == "1.0.0"


def test_a_weight_change_changes_the_config_hash() -> None:
    heavier = RiskModelConfig(
        model_id=MODEL_ID,
        model_version=MODEL_VERSION,
        weights={**DEFAULT_WEIGHTS, "endpoint_availability": 0.30},
    )
    assert heavier.fingerprint() != CFG.fingerprint()


# ------------------------------------------------------------------- providers


def test_posting_age_is_missing_without_a_first_seen_date() -> None:
    result = PostingAgeSignal().evaluate(
        _history(first_seen_at=None), CTX
    )
    assert result.status is SignalStatus.MISSING
    assert result.risk_contribution is None


def test_an_old_posting_alone_is_capped_and_low_confidence() -> None:
    history = _history(first_seen_at=NOW - timedelta(days=400))
    result = PostingAgeSignal().evaluate(history, CTX)
    assert result.risk_contribution == pytest.approx(0.5)
    assert result.confidence == pytest.approx(0.35)


def test_posting_age_uses_a_cohort_baseline() -> None:
    history = _history(
        first_seen_at=NOW - timedelta(days=100),
        cohort=CohortStats(median_days=20.0, sample_size=20, scope="cohort"),
    )
    result = PostingAgeSignal().evaluate(history, CTX)
    assert result.risk_contribution == pytest.approx(0.5)
    assert result.confidence == pytest.approx(0.70)


def test_repost_frequency_needs_a_full_crawl_cycle() -> None:
    history = _history(observed_crawl_cycles=None)
    assert RepostFrequencySignal().evaluate(history, CTX).status is SignalStatus.MISSING


def test_repeated_identical_reposts_add_to_risk() -> None:
    history = _history(
        first_seen_at=NOW - timedelta(days=200),
        observed_crawl_cycles=1,
        reposts=(
            RepostPoint(at=NOW - timedelta(days=100)),
            RepostPoint(at=NOW - timedelta(days=70)),
        ),
    )
    result = RepostFrequencySignal().evaluate(history, CTX)
    assert result.risk_contribution == pytest.approx(0.55)
    assert result.confidence == pytest.approx(0.80)


def test_one_transient_endpoint_failure_is_missing() -> None:
    history = _history(endpoints=(EndpointPoint(at=NOW, state="timeout"),))
    assert (
        EndpointAvailabilitySignal().evaluate(history, CTX).status
        is SignalStatus.MISSING
    )


def test_two_consecutive_transient_failures_are_weak_evidence() -> None:
    history = _history(
        endpoints=(
            EndpointPoint(at=NOW - timedelta(days=2), state="timeout"),
            EndpointPoint(at=NOW, state="error"),
        )
    )
    result = EndpointAvailabilitySignal().evaluate(history, CTX)
    assert result.status is SignalStatus.AVAILABLE
    assert result.risk_contribution == pytest.approx(0.40)


def test_a_blocked_endpoint_is_never_negative_evidence() -> None:
    history = _history(endpoints=(EndpointPoint(at=NOW, state="blocked"),))
    result = EndpointAvailabilitySignal().evaluate(history, CTX)
    assert result.status is SignalStatus.MISSING
    assert "says nothing" in (result.missing_reason or "")


def test_a_closed_application_endpoint_is_strong_evidence() -> None:
    history = _history(endpoints=(EndpointPoint(at=NOW, state="closed_message"),))
    result = EndpointAvailabilitySignal().evaluate(history, CTX)
    assert result.risk_contribution == pytest.approx(0.95)
    assert result.strong is True
    assert result.confidence == pytest.approx(0.90, abs=0.01)


def test_explicit_closure_wording_matches_and_is_strong() -> None:
    history = _history(page_text="Sorry, this job has expired and is no longer open.")
    result = ExplicitClosureSignal().evaluate(history, CTX)
    assert result.risk_contribution == pytest.approx(0.95)
    assert result.strong is True


def test_closure_wording_respects_negation() -> None:
    history = _history(page_text="Applications are not closed; apply today.")
    result = ExplicitClosureSignal().evaluate(history, CTX)
    assert result.status is SignalStatus.AVAILABLE
    assert result.risk_contribution == 0.0


def test_inspected_text_without_closure_wording_is_low_confidence() -> None:
    history = _history(page_text="We are growing the platform team.")
    result = ExplicitClosureSignal().evaluate(history, CTX)
    assert result.risk_contribution == 0.0
    assert result.confidence == pytest.approx(0.25)


def test_company_signals_wait_for_an_adequate_sample() -> None:
    limited = _history(
        company_summary=CompanySummaryView(sample_tier="limited", n_unique=5)
    )
    assert (
        CompanyHiringActivitySignal().evaluate(limited, CTX).status
        is SignalStatus.MISSING
    )

    adequate = _history(
        company_summary=CompanySummaryView(
            sample_tier="adequate",
            n_unique=20,
            window_days=90,
            repost_ratio=0.30,
            inactive_ratio=0.40,
        )
    )
    result = CompanyHiringActivitySignal().evaluate(adequate, CTX)
    assert result.status is SignalStatus.AVAILABLE
    assert result.risk_contribution == pytest.approx(0.70)
    assert result.confidence == pytest.approx(0.483, abs=0.01)


def test_conflicting_source_statuses_are_flagged() -> None:
    history = _history(statuses_by_source={"src_a": "closed", "src_b": "active"})
    result = DuplicateConsistencySignal().evaluate(history, CTX)
    assert result.risk_contribution == pytest.approx(0.8)
    assert result.confidence == pytest.approx(0.8)


def test_unchanged_and_copied_text_raises_staleness() -> None:
    snapshots = tuple(
        SnapshotPoint(
            at=NOW - timedelta(days=days),
            content_hash="same",
            description_text="Fast-paced environment. Team player wanted.",
        )
        for days in (300, 200, 100)
    )
    history = _history(
        first_seen_at=NOW - timedelta(days=320),
        snapshots=snapshots,
        related_jobs=tuple(
            RelatedJob(job_id=f"job_{i}", description_similarity=0.95) for i in range(5)
        ),
        requirements_text="Requirements: build services.",
        description_text="Fast-paced environment. Team player wanted.",
    )
    result = ContentStalenessSignal().evaluate(history, CTX)
    assert result.status is SignalStatus.AVAILABLE
    assert result.risk_contribution > 0.2
    assert result.confidence == pytest.approx(0.45)


def test_implausible_technology_experience_is_flagged() -> None:
    history = _history(
        requirements_text="15+ years of experience with Kubernetes required."
    )
    result = ImplausibleRequirementsSignal().evaluate(history, CTX)
    assert result.risk_contribution == pytest.approx(0.6)
    assert result.evidence[0].data["contradictions"]


def test_entry_level_role_asking_for_a_decade_is_flagged() -> None:
    history = _history(
        requirements_text="Entry level role. 10 years of experience required."
    )
    result = ImplausibleRequirementsSignal().evaluate(history, CTX)
    assert result.risk_contribution == pytest.approx(0.6)
