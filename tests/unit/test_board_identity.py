"""Board identity: the same board written several ways must collapse to one key."""

from __future__ import annotations

import pytest
from app.domain.services.board_identity import board_identity, identity_key


def test_greenhouse_hosts_are_the_same_tenant() -> None:
    assert board_identity(
        "ats.greenhouse", "https://boards.greenhouse.io/acme"
    ) == board_identity("ats.greenhouse", "https://job-boards.greenhouse.io/acme/")


def test_tenant_key_wins_over_the_url() -> None:
    assert identity_key(
        "ats.greenhouse", "https://boards.greenhouse.io/acme", "ACME"
    ) == identity_key("ats.greenhouse", "https://boards.greenhouse.io/other", "acme")


def test_workday_locale_is_a_mirror_not_a_board() -> None:
    english = board_identity(
        "ats.workday", "https://acme.wd1.myworkdayjobs.com/en-US/Careers"
    )
    french = board_identity(
        "ats.workday", "https://acme.wd5.myworkdayjobs.com/fr-CA/Careers/"
    )
    assert english == french == ("ats.workday", "acme", "careers")


def test_workday_sites_are_different_boards() -> None:
    assert board_identity(
        "ats.workday", "https://acme.wd1.myworkdayjobs.com/Careers"
    ) != board_identity("ats.workday", "https://acme.wd1.myworkdayjobs.com/Students")


def test_workday_tenant_key_selects_tenant_and_site() -> None:
    assert board_identity(
        "ats.workday",
        "https://acme.wd1.myworkdayjobs.com/en-US/Careers",
        "acme|Campus",
    ) == ("ats.workday", "acme", "campus")


def test_oracle_locale_is_a_mirror_but_the_site_is_the_board() -> None:
    one = board_identity(
        "ats.oracle_recruiting",
        "https://acme.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/job/1",
    )
    two = board_identity(
        "ats.oracle_recruiting",
        "https://acme.oraclecloud.com/hcmUI/CandidateExperience/fr/sites/CX_1",
    )
    other = board_identity(
        "ats.oracle_recruiting",
        "https://acme.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2",
    )
    assert one == two == ("ats.oracle_recruiting", "acme.oraclecloud.com|cx_1")
    assert other != one


def test_paylocity_slug_is_decorative() -> None:
    guids = "47be28c3-a5c7-4b34-b6b4-89ec95cd3c0d"
    key = identity_key(
        "ats.paylocity",
        f"https://recruiting.paylocity.com/Recruiting/Jobs/All/{guids}",
    )
    assert key == f"ats.paylocity|{guids}"


def test_ultipro_is_identified_by_its_board_guid() -> None:
    board = "0123abcd-0000-0000-0000-000000000000"
    assert board_identity(
        "ats.ultipro", f"https://recruiting.ultipro.com/ACME/JobBoard/{board}/"
    ) == ("ats.ultipro", board)


def test_taleo_sections_are_different_boards() -> None:
    first = board_identity(
        "ats.taleo", "https://acme.taleo.net/careersection/2/jobsearch.ftl?lang=en"
    )
    second = board_identity(
        "ats.taleo", "https://acme.taleo.net/careersection/3/jobsearch.ftl?lang=en"
    )
    assert first == ("ats.taleo", "acme|2")
    assert second != first


@pytest.mark.parametrize(
    "url",
    ["", "https://example.com/", "not a url"],
)
def test_non_board_urls_have_no_identity(url: str) -> None:
    assert board_identity("ats.greenhouse", url) == ()


def test_different_ats_never_collide() -> None:
    assert identity_key("ats.lever", "https://jobs.lever.co/acme", "acme") != (
        identity_key("ats.greenhouse", "https://boards.greenhouse.io/acme", "acme")
    )
