"""Suppression engine rules (spec §8.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.models.application import (
    PRE_APPLICATION_STATUSES,
    AppliedJob,
    SuppressionCandidate,
    SuppressionPolicy,
)
from app.domain.services.suppression import (
    REASON_COMPANY_WIDE,
    REASON_EXACT_REPOST,
    REASON_SIMILAR_ROLE,
    evaluate_suppression,
    similarity_breakdown,
)

NOW = datetime(2026, 6, 1, tzinfo=UTC)

BACKEND = (
    "We are hiring a backend engineer to build distributed services in Python and Go. "
    "You will own reliability, APIs and data models for a growing platform team."
)
ACCOUNTING = (
    "Own the monthly close, reconcile ledgers and prepare statutory filings for the "
    "group. Qualified accountant with IFRS experience required."
)


def _candidate(**over: object) -> SuppressionCandidate:
    values: dict[str, object] = {
        "job_id": "job_new",
        "company_id": "co_1",
        "title": "Backend Engineer",
        "title_family": "engineering",
        "description_text": BACKEND,
        "canonical_url": "https://example.com/jobs/new",
        "department": "Platform",
        "locations": ({"city": "Berlin", "country": "DE"},),
        "workplace_type": "remote",
    }
    values.update(over)
    return SuppressionCandidate(**values)  # type: ignore[arg-type]


def _applied(**over: object) -> AppliedJob:
    values: dict[str, object] = {
        "application_id": "app_1",
        "job_id": "job_applied",
        "company_id": "co_1",
        "title": "Backend Engineer",
        "title_family": "engineering",
        "description_text": BACKEND,
        "canonical_url": "https://example.com/jobs/applied",
        "applied_at": NOW - timedelta(days=10),
        "department": "Platform",
        "locations": ({"city": "Berlin", "country": "DE"},),
        "workplace_type": "remote",
    }
    values.update(over)
    return AppliedJob(**values)  # type: ignore[arg-type]


def test_pre_application_states_are_only_interested_and_saved():
    assert PRE_APPLICATION_STATUSES == ("interested", "saved")


def test_pre_application_rows_never_suppress():
    # Rule 1: no applied_at means no cooldown, whatever the title says.
    decision = evaluate_suppression(
        _candidate(), [_applied(applied_at=None)], SuppressionPolicy(), now=NOW
    )
    assert decision is None


def test_same_job_is_an_exact_repost_without_expiry():
    decision = evaluate_suppression(
        _candidate(job_id="job_applied"), [_applied()], SuppressionPolicy(), now=NOW
    )
    assert decision is not None
    assert decision.reason == REASON_EXACT_REPOST
    assert decision.expires_at is None
    assert decision.evidence["match"] == "same_job"
    assert "same job" in decision.explanation


def test_related_job_id_counts_as_an_exact_repost():
    decision = evaluate_suppression(
        _candidate(job_id="job_repost"),
        [_applied(related_job_ids=("job_repost",))],
        SuppressionPolicy(),
        now=NOW,
    )
    assert decision is not None
    assert decision.reason == REASON_EXACT_REPOST
    assert decision.evidence["match"] == "related_job"


def test_same_canonical_url_counts_as_an_exact_repost():
    decision = evaluate_suppression(
        _candidate(canonical_url="https://example.com/jobs/applied/"),
        [_applied()],
        SuppressionPolicy(),
        now=NOW,
    )
    assert decision is not None
    assert decision.reason == REASON_EXACT_REPOST
    assert decision.evidence["match"] == "same_canonical_url"


def test_exact_repost_can_be_turned_off():
    decision = evaluate_suppression(
        _candidate(job_id="job_applied"),
        [_applied(description_text=ACCOUNTING, title="Accountant", title_family="finance")],
        SuppressionPolicy(exact_repost_suppress=False),
        now=NOW,
    )
    assert decision is None


def test_similar_role_is_suppressed_until_the_cooldown_expires():
    decision = evaluate_suppression(
        _candidate(), [_applied()], SuppressionPolicy(), now=NOW
    )
    assert decision is not None
    assert decision.reason == REASON_SIMILAR_ROLE
    assert decision.expires_at == _applied().applied_at + timedelta(days=180)
    assert decision.similarity >= 0.75
    assert "similar role" in decision.explanation


def test_different_title_family_is_allowed():
    candidate = _candidate(title="Accountant", title_family="finance", description_text=ACCOUNTING)
    decision = evaluate_suppression(candidate, [_applied()], SuppressionPolicy(), now=NOW)
    assert decision is None


def test_low_similarity_inside_the_same_family_is_allowed():
    candidate = _candidate(
        title="Site Reliability Engineer",
        description_text="Own on-call rotations, Terraform modules and incident reviews.",
        department="Infrastructure",
        locations=({"city": "Lisbon", "country": "PT"},),
        workplace_type="hybrid",
    )
    decision = evaluate_suppression(candidate, [_applied()], SuppressionPolicy(), now=NOW)
    assert decision is None


def test_expired_similar_role_cooldown_is_not_suppressed():
    old = _applied(applied_at=NOW - timedelta(days=181))
    decision = evaluate_suppression(_candidate(), [old], SuppressionPolicy(), now=NOW)
    assert decision is None


def test_exact_repost_outlives_a_lapsed_cooldown():
    old = _applied(job_id="job_applied", applied_at=NOW - timedelta(days=400))
    decision = evaluate_suppression(
        _candidate(job_id="job_applied"), [old], SuppressionPolicy(), now=NOW
    )
    assert decision is not None
    assert decision.reason == REASON_EXACT_REPOST


def test_company_wide_cooldown_when_enabled_covers_other_roles():
    policy = SuppressionPolicy(company_wide_cooldown_enabled=True)
    candidate = _candidate(title="Accountant", title_family="finance", description_text=ACCOUNTING)
    decision = evaluate_suppression(candidate, [_applied()], policy, now=NOW)
    assert decision is not None
    assert decision.reason == REASON_COMPANY_WIDE
    assert decision.expires_at == _applied().applied_at + timedelta(days=90)


def test_company_wide_cooldown_stops_after_its_own_window():
    policy = SuppressionPolicy(company_wide_cooldown_enabled=True)
    old = _applied(applied_at=NOW - timedelta(days=91))
    candidate = _candidate(title="Accountant", title_family="finance", description_text=ACCOUNTING)
    decision = evaluate_suppression(candidate, [old], policy, now=NOW)
    assert decision is None


def test_another_company_never_suppresses():
    candidate = _candidate(company_id="co_2", canonical_url="https://example.com/jobs/other")
    decision = evaluate_suppression(
        candidate,
        [_applied()],
        SuppressionPolicy(company_wide_cooldown_enabled=True),
        now=NOW,
    )
    assert decision is None


def test_similarity_breakdown_renormalises_missing_dimensions():
    candidate = _candidate(department=None)
    applied = _applied(department="Platform")
    breakdown = similarity_breakdown(candidate, applied)
    assert "department" not in breakdown
    assert breakdown["score"] == 1.0


def test_similarity_breakdown_reports_low_score_for_a_different_role():
    candidate = _candidate(
        title="Accountant", description_text=ACCOUNTING, department=None
    )
    breakdown = similarity_breakdown(candidate, _applied(department=None))
    assert breakdown["score"] < 0.75


def test_policy_roundtrip_clamps_out_of_range_values():
    policy = SuppressionPolicy.from_mapping(
        {
            "similar_role_threshold": 4.0,
            "similar_role_cooldown_days": -5,
            "company_wide_cooldown_enabled": True,
            "unknown_key": "ignored",
        }
    )
    assert policy.similar_role_threshold == 1.0
    assert policy.similar_role_cooldown_days == 0
    assert policy.company_wide_cooldown_enabled is True
    assert SuppressionPolicy.from_mapping(policy.to_mapping()) == policy
