"""Outcome analytics keeps small samples honest and never scores no-response."""

from __future__ import annotations

from app.application.analytics import RESPONSE_KINDS, _rate


def test_small_groups_are_flagged_as_insufficient() -> None:
    entry = _rate(4, 4)
    assert entry["insufficient"] is True
    assert entry["n"] == 4


def test_five_observations_are_enough_for_an_interval() -> None:
    entry = _rate(5, 10)
    assert entry["insufficient"] is False
    assert entry["interval"] is not None
    low, high = entry["interval"]
    assert low < 0.5 < high


def test_no_response_is_never_a_response_signal() -> None:
    assert "no_response" not in RESPONSE_KINDS
    assert "interview" in RESPONSE_KINDS
    assert "rejection" in RESPONSE_KINDS


def test_an_empty_group_has_no_rate() -> None:
    entry = _rate(0, 0)
    assert entry["rate"] is None
    assert entry["insufficient"] is True
