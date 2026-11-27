"""Area visibility rules behind the ranked shortlist."""
from __future__ import annotations

from app.domain.services.location_prefs import job_in_area, preferred_locations


def test_without_locations_the_rule_is_unknown() -> None:
    assert job_in_area("Berlin, Germany", "onsite", {}) is None
    assert job_in_area("Berlin, Germany", "onsite", {"relocation": False}) is None


def test_relocation_disables_the_area_rule() -> None:
    preferences = {"locations": ["Berlin"], "relocation": True}
    assert job_in_area("Austin, TX", "onsite", preferences) is True


def test_matches_city_tokens_and_metro_area() -> None:
    preferences = {"locations": ["San Francisco Bay Area"], "relocation": False}
    assert job_in_area("San Francisco, CA, United States", "onsite", preferences) is True
    assert job_in_area("New York, NY, United States", "onsite", preferences) is False


def test_country_only_preference_matches_any_city_in_it() -> None:
    preferences = {"locations": ["Germany"], "relocation": False}
    assert job_in_area("Berlin, Germany", "hybrid", preferences) is True
    assert job_in_area("London, United Kingdom", "hybrid", preferences) is False


def test_remote_roles_follow_the_remote_preference() -> None:
    preferences = {"locations": ["Berlin"], "remote_ok": True}
    assert job_in_area("Remote (EU)", "remote", preferences) is True
    # Remote work not wanted: the listed city cannot decide the question.
    assert job_in_area("Remote (EU)", "remote", {"locations": ["Berlin"]}) is None


def test_missing_location_is_unknown_not_a_mismatch() -> None:
    preferences = {"locations": ["Berlin"]}
    assert job_in_area("", "onsite", preferences) is None
    assert job_in_area("unknown", "onsite", preferences) is None


def test_preferred_locations_normalizes_commas_and_duplicates() -> None:
    preferences = {"locations": ["Berlin, London", "berlin", " "]}
    assert preferred_locations(preferences) == ["berlin", "london"]
