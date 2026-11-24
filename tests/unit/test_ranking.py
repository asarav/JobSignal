"""The rank policy matrix, exactly as spec §9.8 describes it."""

from __future__ import annotations

import pytest
from app.domain.services.ranking import (
    credibility_factor,
    explain,
    rank_score,
    visibility,
)


def test_a_factor_of_one_means_credibility_does_not_move_the_row() -> None:
    assert credibility_factor(None, None) == 1.0
    assert credibility_factor(0.9, 0.0) == 1.0


def test_confidence_scales_the_penalty() -> None:
    assert credibility_factor(0.8, 1.0) == pytest.approx(0.6)
    assert credibility_factor(0.8, 0.25) == pytest.approx(0.8)


def test_deprioritize_multiplies_but_show_all_does_not() -> None:
    assert rank_score(0.9, 0.8, 1.0, policy="deprioritize") == pytest.approx(0.54)
    assert rank_score(0.9, 0.8, 1.0, policy="show_all") == pytest.approx(0.9)
    assert rank_score(None, 0.8, 1.0) is None


@pytest.mark.parametrize(
    ("risk_class", "confidence", "policy", "expected"),
    [
        ("likely_inactive", 0.8, "show_all", "shown"),
        ("likely_inactive", 0.8, "deprioritize", "shown"),
        ("likely_inactive", 0.8, "hide_high_risk", "hidden"),
        ("likely_inactive", 0.4, "hide_high_risk", "shown"),
        ("likely_stale", 0.8, "hide_high_risk", "shown"),
        ("likely_active", 0.9, "require_evidence", "shown"),
        ("needs_verification", 0.6, "require_evidence", "hidden"),
        (None, None, "hide_high_risk", "shown"),
    ],
)
def test_visibility_matrix(
    risk_class: str | None, confidence: float | None, policy: str, expected: str
) -> None:
    assert visibility(risk_class, confidence, policy=policy) == expected


def test_an_unknown_policy_falls_back_to_the_documented_default() -> None:
    assert visibility("likely_inactive", 0.9, policy="nonsense") == "shown"


def test_the_explanation_keeps_fit_and_credibility_separate() -> None:
    result = explain(
        fit=0.8,
        risk=0.62,
        confidence=0.55,
        risk_label="May be inactive",
        policy="deprioritize",
        visibility_state="shown",
    )
    text = " ".join(result.reasons)
    assert "Fit 0.80" in text
    assert "risk 0.62" in text
    assert "not hidden" in text
    assert result.rank_score == pytest.approx(0.8 * credibility_factor(0.62, 0.55))


def test_missing_fit_and_missing_risk_are_explained_as_missing() -> None:
    result = explain(
        fit=None,
        risk=None,
        confidence=None,
        risk_label=None,
        policy="deprioritize",
        visibility_state="shown",
    )
    text = " ".join(result.reasons)
    assert "No fit score" in text
    assert "Not assessed" in text
    assert result.rank_score is None
