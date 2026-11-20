"""Seed bootstrap: subdomain boards are parsed without inventing tenants."""

from __future__ import annotations

from scripts.build_seed import Board, _merge, _subdomain_board


def test_workday_board_from_a_locale_host() -> None:
    board = _subdomain_board(
        "ats.workday", "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers/job/123"
    )

    assert board == Board(
        "ats.workday",
        "acme",
        "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers",
        tenant_key="acme",
    )


def test_workday_board_without_a_shard() -> None:
    board = _subdomain_board(
        "ats.workday", "https://acme.myworkdayjobs.com/en-GB/careers"
    )

    assert board is not None
    assert board.tenant == "acme"
    assert board.url == "https://acme.myworkdayjobs.com/en-GB/careers"


def test_workday_assets_and_unlocalised_paths_are_not_boards() -> None:
    assert (
        _subdomain_board(
            "ats.workday", "https://nxp.wd3.myworkdayjobs.com/assets/images/logo.png"
        )
        is None
    )
    assert (
        _subdomain_board("ats.workday", "https://acme.wd5.myworkdayjobs.com/careers")
        is None
    )


def test_icims_board_keeps_the_tenant_prefix() -> None:
    board = _subdomain_board(
        "ats.icims", "https://careers-acme.icims.com/jobs/1234/engineer/job"
    )

    assert board == Board("ats.icims", "careers-acme", "https://careers-acme.icims.com")


def test_icims_platform_hosts_are_not_tenants() -> None:
    for url in (
        "https://www.icims.com/jobs/search",
        "https://login.icims.com/u/login/identifier",
        "https://assets.icims.com/portal.css",
        "https://deep.careers.icims.com/jobs/1/job",
    ):
        assert _subdomain_board("ats.icims", url) is None, url


def test_merge_collapses_the_same_tenant_across_hosts() -> None:
    """One tenant discovered on two Workday shards is still one board."""

    first = Board(
        "ats.workday",
        "acme",
        "https://acme.wd1.myworkdayjobs.com/en-US/careers",
        tenant_key="acme",
    )
    second = Board(
        "ats.workday",
        "acme",
        "https://acme.wd5.myworkdayjobs.com/en-US/careers",
        tenant_key="acme",
    )

    merged = _merge({first: 3}, {second: 5})

    assert len(merged) == 1
    assert sum(merged.values()) == 8
