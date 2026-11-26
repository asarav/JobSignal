"""Domain-index targets, dense queries and yield-weighted slice allocation."""

from __future__ import annotations

from app.plugins.discovery.domain_index import (
    BUCKETS,
    DENSE_FILTERS,
    DomainIndexProvider,
    IndexTarget,
    _allocate,
    _candidate,
    _cdx_url,
)

WORKDAY = IndexTarget("myworkdayjobs.com", "ats.workday", "domain")
BAMBOO = IndexTarget("bamboohr.com", "ats.bamboohr", "domain")


def test_jazzhr_board_is_parsed() -> None:
    candidate = _candidate("ats.jazzhr", "https://acme.applytojob.com/apply")
    assert candidate is not None
    assert candidate.url == "https://acme.applytojob.com"
    assert candidate.tenant == "acme"


def test_taleo_section_is_part_of_the_board() -> None:
    candidate = _candidate(
        "ats.taleo", "https://acme.taleo.net/careersection/2/jobsearch.ftl?lang=en"
    )
    assert candidate is not None
    assert candidate.url == (
        "https://acme.taleo.net/careersection/2/jobsearch.ftl?lang=en"
    )
    assert candidate.tenant == "acme|2"


def test_paylocity_guid_is_the_tenant() -> None:
    guid = "47be28c3-a5c7-4b34-b6b4-89ec95cd3c0d"
    candidate = _candidate(
        "ats.paylocity", f"https://recruiting.paylocity.com/Recruiting/Jobs/All/{guid}"
    )
    assert candidate is not None
    assert candidate.tenant == guid
    assert guid in candidate.url


def test_dense_query_carries_the_targets_exclusions() -> None:
    plain = _cdx_url(WORKDAY, "a", 10_000)
    dense = _cdx_url(WORKDAY, "a", 10_000, dense=True)
    assert "filter=" not in plain or "%21original" not in plain
    assert "limit=10000" in dense
    # One base leading-character filter plus one clause per exclusion.
    assert dense.count("filter=") == len(DENSE_FILTERS["myworkdayjobs.com"]) + 1
    assert dense != plain


def test_target_without_a_dense_shape_falls_back_to_plain() -> None:
    target = IndexTarget("example-ats.com", "ats.example", "domain")
    assert _cdx_url(target, "a", 10, dense=True) == _cdx_url(target, "a", 10)


def test_allocation_keeps_every_target_explored() -> None:
    shares = _allocate(10, [WORKDAY, BAMBOO], {"ats.workday": 50.0})
    counts = {target.connector_id: count for target, count in shares}
    assert sum(counts.values()) == 10
    assert counts["ats.workday"] > counts["ats.bamboohr"]
    assert counts["ats.bamboohr"] >= 1


def test_uniform_rotation_covers_every_target_bucket() -> None:
    provider = DomainIndexProvider(targets=(WORKDAY, BAMBOO), slices_per_pass=2)
    first = provider.slices_for(0, 2)
    assert [bucket for _, bucket in first] == ["a", "b"]
    # The flat rotation walks Workday's buckets, then BambooHR's.
    later = provider.slices_for(36, 2)
    assert {target.connector_id for target, _ in later} == {"ats.bamboohr"}


def test_weighted_rotation_prefers_the_productive_target() -> None:
    provider = DomainIndexProvider(targets=(WORKDAY, BAMBOO), slices_per_pass=4)
    pairs = provider.slices_for(0, 1, {"ats.workday": 8.0, "ats.bamboohr": 1.0})
    workday = sum(1 for target, _ in pairs if target.connector_id == "ats.workday")
    bamboo = sum(1 for target, _ in pairs if target.connector_id == "ats.bamboohr")
    assert (workday, bamboo) == (3, 1)


def test_weighted_rotation_stays_inside_the_bucket_space() -> None:
    provider = DomainIndexProvider(targets=(WORKDAY,), slices_per_pass=6)
    pairs = provider.slices_for(0, 1, {"ats.workday": 3.0})
    assert all(bucket in BUCKETS for _, bucket in pairs)
    assert len({bucket for _, bucket in pairs}) == len(pairs)
