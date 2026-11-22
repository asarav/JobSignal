"""Seed bootstrap: subdomain boards are parsed without inventing tenants."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import scripts.build_seed as build_seed
from scripts.build_seed import (
    SEED_ATS_HOSTS,
    SUBDOMAIN_ATS,
    Board,
    _chunk_urls,
    _interleave,
    _merge,
    _new_only,
    _row_count,
    _subdomain_board,
    _ultipro_board,
)


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


def test_workday_assets_are_not_boards_but_a_locale_less_site_is() -> None:
    """``/{site}`` is served for real boards; ``/assets/...`` is plumbing."""

    assert (
        _subdomain_board(
            "ats.workday", "https://nxp.wd3.myworkdayjobs.com/assets/images/logo.png"
        )
        is None
    )
    locale_less = _subdomain_board(
        "ats.workday", "https://acme.wd5.myworkdayjobs.com/careers"
    )

    assert locale_less is not None
    assert locale_less.url == "https://acme.wd5.myworkdayjobs.com/careers"


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


def test_merge_keeps_every_workday_career_site_of_one_tenant() -> None:
    """A Workday tenant's sites are separate boards, not one employer-wide one."""

    mountain = Board(
        "ats.workday",
        "alterra",
        "https://alterra.wd1.myworkdayjobs.com/en-US/AlterraMountainCompany",
        tenant_key="alterra",
    )
    resort = Board(
        "ats.workday",
        "alterra",
        "https://alterra.wd1.myworkdayjobs.com/en-US/BigBearMountainResort",
        tenant_key="alterra",
    )

    merged = _merge({mountain: 4}, {resort: 9})

    assert {board.url for board in merged} == {mountain.url, resort.url}
    assert sorted(merged.values()) == [4, 9]


def test_merge_collapses_locale_variants_of_one_workday_site() -> None:
    """The same Workday site in two languages is still one board."""

    english = Board(
        "ats.workday",
        "acme",
        "https://acme.wd1.myworkdayjobs.com/en-US/Careers",
        tenant_key="acme",
    )
    french = Board(
        "ats.workday",
        "acme",
        "https://acme.wd1.myworkdayjobs.com/fr-CA/Careers",
        tenant_key="acme",
    )

    merged = _merge({english: 2}, {french: 3})

    assert len(merged) == 1
    assert sum(merged.values()) == 5


def test_bamboohr_board_is_the_company_subdomain() -> None:
    board = _subdomain_board(
        "ats.bamboohr", "https://acme.bamboohr.com/careers/42/software-engineer"
    )

    assert board == Board(
        "ats.bamboohr", "acme", "https://acme.bamboohr.com/careers", tenant_key="acme"
    )


def test_bamboohr_platform_hosts_are_not_tenants() -> None:
    for url in (
        "https://www.bamboohr.com/pricing",
        "https://api.bamboohr.com/api/gateway.php/acme/v1/employees",
        "https://analytics-use2-1.bamboohr.com/collect",
        "https://deep.careers.bamboohr.com/careers",
    ):
        assert _subdomain_board("ats.bamboohr", url) is None, url


def test_incremental_skips_boards_the_seed_already_covers() -> None:
    shipped = {
        Board(
            "ats.workday",
            "acme",
            "https://acme.wd1.myworkdayjobs.com/en-US/Careers",
            tenant_key="acme",
        ): 1
    }
    candidates = {
        # Same board, other locale: already covered.
        Board(
            "ats.workday",
            "acme",
            "https://acme.wd1.myworkdayjobs.com/fr-CA/Careers",
            tenant_key="acme",
        ): 3,
        # A second career site of the same tenant: not covered.
        Board(
            "ats.workday",
            "acme",
            "https://acme.wd1.myworkdayjobs.com/en-US/AcmeLabs",
            tenant_key="acme",
        ): 5,
    }

    new = _new_only(candidates, shipped)

    assert [board.url for board in new] == [
        "https://acme.wd1.myworkdayjobs.com/en-US/AcmeLabs"
    ]


def test_incremental_restores_a_live_board_missing_from_the_seed() -> None:
    """A cache entry that says "live" must not hide a board the file lost."""

    board = Board(
        "ats.workday",
        "acme",
        "https://acme.wd1.myworkdayjobs.com/en-US/Careers",
        tenant_key="acme",
    )
    dead = Board(
        "ats.workday",
        "gone",
        "https://gone.wd1.myworkdayjobs.com/en-US/Careers",
        tenant_key="gone",
    )
    checked = {"ats.workday|acme|careers": 12, "ats.workday|gone|careers": -1}

    new = _new_only({board: 1, dead: 1}, {}, checked)

    assert list(new) == [board]


def test_new_rows_with_postings_go_above_boards_without_any() -> None:
    existing = [
        {"url": "https://acme.test/big", "confidence": 0.9},
        {"url": "https://acme.test/empty", "confidence": 0.6},
    ]
    new = [
        {"url": "https://new.test/hiring", "confidence": 0.9},
        {"url": "https://new.test/quiet", "confidence": 0.6},
    ]

    merged = _interleave(existing, new)

    assert [row["url"] for row in merged] == [
        "https://acme.test/big",
        "https://new.test/hiring",
        "https://acme.test/empty",
        "https://new.test/quiet",
    ]


def test_locale_less_workday_boards_do_not_collide_on_one_site() -> None:
    first = _subdomain_board("ats.workday", "https://acme.wd1.myworkdayjobs.com/Acme")
    second = _subdomain_board("ats.workday", "https://acme.wd1.myworkdayjobs.com/AcmeBrands")
    localised = _subdomain_board(
        "ats.workday", "https://acme.wd1.myworkdayjobs.com/en-US/Acme"
    )

    assert first is not None and second is not None and localised is not None
    assert first.dedupe_key != second.dedupe_key
    assert first.dedupe_key == localised.dedupe_key


def test_workday_index_query_excludes_urls_that_cannot_name_a_board() -> None:
    """The walk's density depends on these clauses staying in the query."""

    filters = SUBDOMAIN_ATS["ats.workday"]["filters"]
    joined = " ".join(filters)

    assert r"\?" in joined, "query strings are crawl noise"
    assert "job/" in joined
    assert ".well-known" in joined


def test_a_query_string_does_not_change_a_board() -> None:
    plain = _subdomain_board("ats.workday", "https://acme.wd1.myworkdayjobs.com/Acme")
    noisy = _subdomain_board(
        "ats.workday", "https://acme.wd1.myworkdayjobs.com/Acme?utm_source=x&page=2"
    )

    assert plain is not None and noisy is not None
    assert plain.dedupe_key == noisy.dedupe_key


def test_workday_accepts_a_locale_less_career_site() -> None:
    """``/{site}`` is a real Workday board, and only one of its URL forms."""

    board = _subdomain_board("ats.workday", "https://acrisure.wd1.myworkdayjobs.com/Acrisure")

    assert board is not None
    assert board.tenant == "acrisure"
    assert board.url == "https://acrisure.wd1.myworkdayjobs.com/Acrisure"


def test_workday_reads_chrome_as_the_site_it_belongs_to() -> None:
    board = _subdomain_board(
        "ats.workday", "https://abcfws.wd1.myworkdayjobs.com/abcfws/siteMap.xml"
    )
    chrome = _subdomain_board(
        "ats.workday", "https://acg.wd1.myworkdayjobs.com/.well-known/ai-plugin.json"
    )

    assert board is not None
    assert board.url == "https://abcfws.wd1.myworkdayjobs.com/abcfws"
    assert chrome is None


def test_workday_needs_a_site_and_a_tenant() -> None:
    assert _subdomain_board("ats.workday", "http://acg.wd1.myworkdayjobs.com/") is None
    mangled = "https://abcfws.wd1.myworkdayjobs.com/abcfws&amp;x"

    assert _subdomain_board("ats.workday", mangled) is None


def test_workday_unmangles_an_escaped_protocol_relative_host() -> None:
    board = _subdomain_board(
        "ats.workday", "http://252fbaltimorecity.wd1.myworkdayjobs.com/en-US/City"
    )

    assert board is not None
    assert board.tenant == "baltimorecity"
    assert board.url == "https://baltimorecity.wd1.myworkdayjobs.com/en-US/City"


def test_a_garbled_cdx_body_is_not_an_empty_result() -> None:
    """An error page must not be mistaken for "this shard ended"."""

    assert _row_count("<html><title>Internet Archive: Temporarily Offline</title>") is None
    assert _row_count("") is None
    assert _row_count("[]") == 0

    urls, key, parsed = _chunk_urls("<html>offline</html>")
    assert (urls, key, parsed) == ([], None, False)


def test_a_valid_cdx_chunk_reports_its_rows_key_and_parse() -> None:
    body = '[["original"], ["https://acme.wd1.myworkdayjobs.com/en-US/Careers"]]'

    urls, key, parsed = _chunk_urls(body)

    assert parsed is True
    assert key is None
    assert urls == ["https://acme.wd1.myworkdayjobs.com/en-US/Careers"]
    assert _row_count(body) == 1


def test_ultipro_board_from_a_detail_url() -> None:
    board = _ultipro_board(
        "https://recruiting2.ultipro.com/acme01/JobBoard/"
        "6B442184-52A5-4635-8DB5-5AA4BED2A563/OpportunityDetail?opportunityId=x"
    )

    assert board == Board(
        "ats.ultipro",
        "ACME01",
        "https://recruiting2.ultipro.com/ACME01/JobBoard/"
        "6b442184-52a5-4635-8db5-5aa4bed2a563",
        tenant_key="6b442184-52a5-4635-8db5-5aa4bed2a563",
    )


def test_ultipro_platform_and_non_board_urls_are_not_boards() -> None:
    for url in (
        "https://www.ultipro.com/pricing",
        "https://recruiting.ultipro.com/ACME01/JobBoard/images",
        "https://recruiting.ultipro.com/ACME01",
        "https://recruiting.ultipro.com/ACME01/JobBoard/not-a-board-id",
    ):
        assert _ultipro_board(url) is None, url


def test_oracle_board_is_the_site_not_the_locale() -> None:
    """One Oracle tenant host can publish several sites; each is a board."""

    english = build_seed._oracle_board(
        "https://cbcz.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/requisitions"
    )
    french = build_seed._oracle_board(
        "https://cbcz.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/fr/sites/CX"
    )
    campus = build_seed._oracle_board(
        "https://cbcz.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
    )

    assert english is not None and french is not None and campus is not None
    assert english.url.startswith("https://cbcz.fa.us2.oraclecloud.com/hcmUI/")
    assert english.dedupe_key == french.dedupe_key, "a locale mirrors the same site"
    assert english.dedupe_key != campus.dedupe_key, "another site is another board"
    assert english.tenant_key == "cbcz.fa.us2.oraclecloud.com|CX"


def test_oracle_boards_are_walked_through_the_resumable_pod_walker() -> None:
    """Each Oracle pod is a root with its own resume key, not one big query."""

    cfg = SUBDOMAIN_ATS["ats.oracle_recruiting"]
    url = (
        "https://cbcz.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX"
    )

    assert set(cfg["roots"]) == set(build_seed.ORACLE_PODS)
    assert build_seed._subdomain_board("ats.oracle_recruiting", url) == build_seed._oracle_board(
        url
    )


def test_oracle_needs_a_pod_host_and_a_candidate_experience_path() -> None:
    for url in (
        "https://fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX",
        "https://cbcz.fa.us2.oraclecloud.com/hcmRestApi/resources/latest/x",
        "https://acme.example.com/hcmUI/CandidateExperience/en/sites/CX",
        "https://cbcz.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/",
    ):
        assert build_seed._oracle_board(url) is None, url


def test_bamboo_index_query_drops_job_pages_to_name_more_tenants() -> None:
    """A chunk should name tenant roots, not one tenant's job pages."""

    filters = SUBDOMAIN_ATS["ats.bamboohr"]["filters"]
    joined = " ".join(filters)

    assert "www" in joined, "BambooHR's own marketing site is not a customer board"
    assert r"/[a-z]{2,}/" in joined, "deep paths spend a whole chunk on one tenant"


class _OfflineThenJson:
    """Stub CDX client: answer the first ``failures`` requests with an error."""

    def __init__(self, failures: int, body: str, status: int = 503) -> None:
        self.failures = failures
        self.body = body
        self.status = status
        self.queries: list[str] = []

    async def get(self, url: str, timeout: float | None = None) -> httpx.Response:
        self.queries.append(url)
        if self.failures:
            self.failures -= 1
            return httpx.Response(self.status, text="<html>offline</html>")
        return httpx.Response(200, text=self.body)


def _clauses(query: str) -> list[str]:
    """The CDX ``filter`` clauses a request was built with."""

    return parse_qs(urlsplit(query).query).get("filter", [])


def test_fixed_host_harvest_walks_slices_that_never_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resumed run must retry the slices the gateway never answered."""

    host = SEED_ATS_HOSTS[0][0]
    every = [[h, b] for h, _connector in SEED_ATS_HOSTS for b in build_seed.BUCKETS]
    kept = [pair for pair in every if pair != [host, "q"]]
    (tmp_path / "cache.json").write_text(
        json.dumps({"completed": kept, "results": {}}), encoding="utf-8"
    )
    monkeypatch.setattr(build_seed, "CACHE_PATH", tmp_path / "cache.json")
    monkeypatch.setattr(build_seed, "LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(build_seed, "REQUEST_GAP_S", 0.0)
    client = _OfflineThenJson(
        failures=0, body='[["original"], ["https://boards.greenhouse.io/acme"]]'
    )

    boards = asyncio.run(build_seed.harvest_all(client))  # type: ignore[arg-type]

    assert len(client.queries) == 1, "only the unanswered slice is queried"
    assert [board.tenant for board in boards] == ["acme"]


def test_bucket_sweep_spends_no_request_once_its_budget_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sweep must stop on its clock even when every bucket is unanswered."""

    monkeypatch.setattr(build_seed, "SUBDOMAIN_CACHE_PATH", tmp_path / "sub.json")
    monkeypatch.setattr(build_seed, "LOG_PATH", tmp_path / "log.jsonl")
    client = _OfflineThenJson(failures=0, body="[]")

    boards = asyncio.run(
        build_seed.harvest_initial_buckets(client, "ats.bamboohr", False, 0.0)  # type: ignore[arg-type]
    )

    assert boards == {}
    assert client.queries == []


def test_ultipro_walk_retries_a_throttled_chunk_instead_of_ending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One bad gateway window used to throw away the whole paginated walk."""

    monkeypatch.setattr(build_seed, "SUBDOMAIN_CACHE_PATH", tmp_path / "sub.json")
    monkeypatch.setattr(build_seed, "LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(build_seed, "REQUEST_GAP_S", 0.0)
    monkeypatch.setattr(build_seed, "THROTTLE_BACKOFF_S", (0.0, 0.0, 0.0, 0.0))
    client = _OfflineThenJson(
        failures=build_seed.HARVEST_ATTEMPTS,
        body='[["original"], ["https://recruiting.ultipro.com/ACME01/JobBoard/'
        '6B442184-52A5-4635-8DB5-5AA4BED2A563/OpportunityDetail"]]',
    )

    boards = asyncio.run(build_seed.harvest_ultipro(client, 2, False))  # type: ignore[arg-type]

    assert len(boards) == 1
    assert len(client.queries) == build_seed.HARVEST_ATTEMPTS + 1
    logged = (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line)["event"] for line in logged]
    assert "chunk_unanswered" in events
    assert "chunk_retrying" in events


def test_bucket_sweep_tries_the_dense_clause_before_the_plain_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bucket asks for tenant roots first, and still lands on the plain scan."""

    monkeypatch.setattr(build_seed, "SUBDOMAIN_CACHE_PATH", tmp_path / "sub.json")
    monkeypatch.setattr(build_seed, "LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(build_seed, "REQUEST_GAP_S", 0.0)
    monkeypatch.setattr(build_seed, "THROTTLE_BACKOFF_S", (0.0, 0.0, 0.0, 0.0))
    # The sweep gives each shape two attempts, so the first bucket's dense
    # query gives up after two rows and the third request is its fallback.
    client = _OfflineThenJson(failures=2, body='[["original"]]')

    boards = asyncio.run(
        build_seed.harvest_initial_buckets(client, "ats.bamboohr", False)  # type: ignore[arg-type]
    )

    assert boards == {}
    dense = _clauses(client.queries[0])
    plain = _clauses(client.queries[2])
    assert len(dense) == 1 + len(SUBDOMAIN_ATS["ats.bamboohr"]["filters"])
    assert len(plain) == 1
    assert dense[0] == plain[0], "the fallback must ask about the same bucket"
    state = json.loads((tmp_path / "sub.json").read_text(encoding="utf-8"))
    assert len(state["ats.bamboohr|buckets"]["buckets"]) == len(build_seed.BUCKETS)
