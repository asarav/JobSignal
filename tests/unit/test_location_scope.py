"""Remote eligibility is parsed from the posting, not assumed to be global."""

from __future__ import annotations

from app.domain.services.location_prefs import job_in_area
from app.domain.services.location_scope import (
    candidate_countries,
    parse_location_scope,
    scope_allows,
)


def test_remote_country_forms_are_parsed() -> None:
    for location in ("Remote - India", "Remote (India)", "India - Remote"):
        scope = parse_location_scope(location, workplace_type="remote")
        assert scope["eligible_countries"] == ["IN"]
        assert scope["remote_scope"] == "country"
        assert scope_allows(scope, ["US"]) is False
        assert scope_allows(scope, ["IN"]) is True


def test_prose_restrictions_and_regions_are_parsed() -> None:
    africa = parse_location_scope(
        "Remote", "Candidates must be located in Africa.", workplace_type="remote"
    )
    assert africa["eligible_countries"] == ["AFRICA"]
    assert scope_allows(africa, ["US"]) is False
    assert scope_allows(africa, ["NG"]) is True

    authorized = parse_location_scope(
        "Remote",
        "You must be authorized to work in the United States.",
        workplace_type="remote",
    )
    assert authorized["eligible_countries"] == ["US"]
    assert scope_allows(authorized, ["US"]) is True


def test_global_and_unknown_are_not_mismatches() -> None:
    anywhere = parse_location_scope(
        "Remote", "You can work from anywhere.", workplace_type="remote"
    )
    assert anywhere["remote_scope"] == "global"
    assert scope_allows(anywhere, ["US"]) is True

    unknown = parse_location_scope("Remote", "Great team.", workplace_type="remote")
    assert unknown["remote_scope"] == "unknown"
    assert unknown["eligible_countries"] == []
    assert scope_allows(unknown, ["US"]) is None


def test_chicago_profile_hides_india_remote_but_keeps_us_and_unknown() -> None:
    preferences = {
        "locations": ["Chicago, IL"],
        "home_location": "Chicago, IL",
        "remote_ok": True,
        "relocation": False,
    }
    assert candidate_countries(preferences) == ["US"]

    india = {"location_scope": parse_location_scope("Remote - India")}
    us = {"location_scope": parse_location_scope("Remote (US)")}
    unknown = {"location_scope": parse_location_scope("Remote")}
    assert job_in_area("Remote - India", "remote", preferences, india) is False
    assert job_in_area("Remote (US)", "remote", preferences, us) is True
    assert job_in_area("Remote", "remote", preferences, unknown) is None

    assert (
        job_in_area("Remote - India", "remote", {**preferences, "relocation": True}, india)
        is True
    )
