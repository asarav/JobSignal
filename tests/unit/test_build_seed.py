"""Seed bootstrap: subdomain boards are parsed without inventing tenants."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
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
    _merge,
    _merge_records,
    _new_only,
    _paylocity_board,
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
    board = _subdomain_board("ats.workday", "https://acme.myworkdayjobs.com/en-GB/careers")

    assert board is not None
    assert board.tenant == "acme"
    assert board.url == "https://acme.myworkdayjobs.com/en-GB/careers"


def test_workday_assets_are_not_boards_but_a_locale_less_site_is() -> None:
    """``/{site}`` is served for real boards; ``/assets/...`` is plumbing."""

    assert (
        _subdomain_board("ats.workday", "https://nxp.wd3.myworkdayjobs.com/assets/images/logo.png")
        is None
    )
    locale_less = _subdomain_board("ats.workday", "https://acme.wd5.myworkdayjobs.com/careers")

    assert locale_less is not None
    assert locale_less.url == "https://acme.wd5.myworkdayjobs.com/careers"


def test_icims_board_keeps_the_tenant_prefix() -> None:
    board = _subdomain_board("ats.icims", "https://careers-acme.icims.com/jobs/1234/engineer/job")

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

    assert [board.url for board in new] == ["https://acme.wd1.myworkdayjobs.com/en-US/AcmeLabs"]


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


def test_new_records_merge_by_board_identity_not_url() -> None:
    """A Workday board seen through another locale replaces, not duplicates."""
    existing = [
        build_seed.SeedRecord(
            "ats.workday",
            "https://acme.wd1.myworkdayjobs.com/en-US/Careers",
            postings=4,
            verified_at="2026-11-01",
            tenant="acme",
            tenant_key="acme",
        )
    ]
    fresh = [
        build_seed.SeedRecord(
            "ats.workday",
            "https://acme.wd1.myworkdayjobs.com/fr-CA/Careers",
            postings=9,
            verified_at="2026-11-22",
            tenant="acme",
            tenant_key="acme",
        )
    ]

    merged = _merge_records(fresh, existing)

    assert len(merged) == 1
    assert merged[0].postings == 9
    assert merged[0].verified_at == "2026-11-22"


def test_locale_less_workday_boards_do_not_collide_on_one_site() -> None:
    first = _subdomain_board("ats.workday", "https://acme.wd1.myworkdayjobs.com/Acme")
    second = _subdomain_board("ats.workday", "https://acme.wd1.myworkdayjobs.com/AcmeBrands")
    localised = _subdomain_board("ats.workday", "https://acme.wd1.myworkdayjobs.com/en-US/Acme")

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
        "https://recruiting2.ultipro.com/ACME01/JobBoard/6b442184-52a5-4635-8db5-5aa4bed2a563",
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
    assert english.tenant_key == "cbcz.fa.us2.oraclecloud.com|cx"


def test_oracle_site_codes_differing_only_in_case_are_one_board() -> None:
    """The index spells one site several ways; that must not seed it twice."""

    upper = build_seed._oracle_board(
        "https://eccs.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/nl/sites/WerkenBij"
    )
    lower = build_seed._oracle_board(
        "https://eccs.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/nl/sites/werkenbij"
    )

    assert upper is not None and lower is not None
    assert upper.dedupe_key == lower.dedupe_key


def test_oracle_boards_are_walked_through_the_resumable_pod_walker() -> None:
    """Each Oracle pod is a root with its own resume key, not one big query."""

    cfg = SUBDOMAIN_ATS["ats.oracle_recruiting"]
    url = "https://cbcz.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX"

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

    boards = asyncio.run(
        build_seed.harvest_path_boards(client, "ats.ultipro", 2, False)  # type: ignore[arg-type]
    )

    assert len(boards) == 1
    assert len(client.queries) == build_seed.HARVEST_ATTEMPTS + 1
    logged = (tmp_path / "log.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line)["event"] for line in logged]
    assert "chunk_unanswered" in events
    assert "chunk_retrying" in events


def test_bucket_sweep_tries_the_plain_clause_before_the_dense_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bucket asks for tenant roots with the cheap scan, then the dense one."""

    monkeypatch.setattr(build_seed, "SUBDOMAIN_CACHE_PATH", tmp_path / "sub.json")
    monkeypatch.setattr(build_seed, "LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(build_seed, "REQUEST_GAP_S", 0.0)
    monkeypatch.setattr(build_seed, "THROTTLE_BACKOFF_S", (0.0, 0.0, 0.0, 0.0))
    # The sweep gives a whole bucket two attempts per shape, so the cheap scan
    # gives up after two rows and the third request is its dense fallback.
    client = _OfflineThenJson(failures=2, body='[["original"]]')

    boards = asyncio.run(
        build_seed.harvest_initial_buckets(client, "ats.bamboohr", False)  # type: ignore[arg-type]
    )

    assert boards == {}
    plain = _clauses(client.queries[0])
    dense = _clauses(client.queries[2])
    assert len(plain) == 1
    assert len(dense) == 1 + len(SUBDOMAIN_ATS["ats.bamboohr"]["filters"])
    assert dense[0] == plain[0], "the fallback must ask about the same bucket"
    state = json.loads((tmp_path / "sub.json").read_text(encoding="utf-8"))
    assert len(state["ats.bamboohr|buckets"]["buckets"]) == len(build_seed.BUCKETS)


def test_paylocity_board_drops_the_decorative_slug() -> None:
    # Measured: one company id is indexed under two different slugs, so both
    # rows have to fold onto the same board.
    first = _paylocity_board(
        "https://recruiting.paylocity.com/Recruiting/Jobs/All/"
        "b280c13f-ea5c-402a-9352-398ddb2c0119/LayerCake"
    )
    second = _paylocity_board(
        "https://recruiting.paylocity.com/recruiting/jobs/all/"
        "B280C13F-EA5C-402A-9352-398DDB2C0119/SKYNET-INNOVATIONS"
    )

    assert (
        first
        == second
        == Board(
            "ats.paylocity",
            "b280c13f-ea5c-402a-9352-398ddb2c0119",
            "https://recruiting.paylocity.com/Recruiting/Jobs/All/"
            "b280c13f-ea5c-402a-9352-398ddb2c0119",
            tenant_key="b280c13f-ea5c-402a-9352-398ddb2c0119",
        )
    )


def test_paylocity_board_needs_a_company_id() -> None:
    assert _paylocity_board("https://recruiting.paylocity.com/Recruiting/Jobs/All") is None
    assert (
        _paylocity_board("https://recruiting.paylocity.com/Recruiting/Jobs/Details/1139001") is None
    )
    assert (
        _paylocity_board(
            "https://example.com/Recruiting/Jobs/All/b280c13f-ea5c-402a-9352-398ddb2c0119"
        )
        is None
    )


def test_jazzhr_board_is_the_tenant_apply_page() -> None:
    assert _subdomain_board(
        "ats.jazzhr", "https://acme.applytojob.com/apply/jobs/details/AbC123"
    ) == Board("ats.jazzhr", "acme", "https://acme.applytojob.com/apply", tenant_key="acme")


def test_jazzhr_board_rejects_the_platform_host() -> None:
    assert _subdomain_board("ats.jazzhr", "https://www.jazzhr.com/") is None
    assert _subdomain_board("ats.jazzhr", "https://applytojob.com/apply") is None


def test_checkpoints_are_written_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cache.json"
    monkeypatch.setattr(build_seed, "CACHE_PATH", path)
    board = Board("ats.greenhouse", "acme", "https://boards.greenhouse.io/acme")

    build_seed._checkpoint({("boards.greenhouse.io", "a")}, {board: 3})

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["completed"] == [["boards.greenhouse.io", "a"]]
    assert raw["results"] == {"ats.greenhouse|acme": 3}
    assert not (tmp_path / "cache.json.tmp").exists()


def test_an_interrupted_checkpoint_keeps_the_last_complete_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "cache.json"
    monkeypatch.setattr(build_seed, "CACHE_PATH", path)
    build_seed._checkpoint({("h", "a")}, {})
    before = path.read_text(encoding="utf-8")

    def explode(_tmp: Path, _path: Path) -> None:
        raise OSError("interrupted before the rename")

    monkeypatch.setattr(build_seed, "_replace_file", explode)
    with pytest.raises(OSError):
        build_seed._checkpoint({("h", "b")}, {})

    assert path.read_text(encoding="utf-8") == before
    assert not (tmp_path / "cache.json.tmp").exists()


@pytest.mark.asyncio
async def test_verification_persists_progress_during_a_long_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_seed, "VERIFIED_PATH", tmp_path / "verified.json")
    monkeypatch.setattr(build_seed, "LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(build_seed, "VERIFY_CHECKPOINT_EVERY", 2)
    boards = {
        Board("ats.greenhouse", f"t{n}", f"https://boards.greenhouse.io/t{n}"): 1 for n in range(6)
    }

    async def fake_verify(client: object, board: Board, sem: object) -> int | None:
        return 0 if board.tenant in {"t0", "t1"} else None

    monkeypatch.setattr(build_seed, "verify_board", fake_verify)

    verified, outcomes = await build_seed.verify_all(None, boards)  # type: ignore[arg-type]

    checked = build_seed._read_verified()
    assert set(checked) == set(outcomes)
    assert len(verified) == 2
    assert sum(1 for entry in checked.values() if entry["count"] == -1) == 4


@pytest.mark.asyncio
async def test_dry_run_verification_does_not_write_the_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "verified.json"
    monkeypatch.setattr(build_seed, "VERIFIED_PATH", cache)
    monkeypatch.setattr(build_seed, "LOG_PATH", tmp_path / "log.jsonl")
    board = Board("ats.greenhouse", "acme", "https://boards.greenhouse.io/acme")

    async def fake_verify(client: object, target: Board, sem: object) -> int:
        return 2

    monkeypatch.setattr(build_seed, "verify_board", fake_verify)

    await build_seed.verify_all(None, {board: 1}, checkpoint=False)  # type: ignore[arg-type]

    assert not cache.exists()


def test_live_verifications_resume_after_an_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_seed, "VERIFIED_PATH", tmp_path / "verified.json")
    board = Board(
        "ats.workday",
        "acme",
        "https://acme.wd1.myworkdayjobs.com/Careers",
        "acme|Careers",
    )
    key = build_seed._cache_key(board)

    build_seed._checkpoint_verified({key: 5}, None, {key: board})

    assert build_seed._resume_live(build_seed._read_verified()) == [(board, 5)]


def test_old_live_verifications_are_checked_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "verified.json"
    monkeypatch.setattr(build_seed, "VERIFIED_PATH", path)
    board = Board("ats.greenhouse", "acme", "https://boards.greenhouse.io/acme")
    path.write_text(
        json.dumps(
            {
                build_seed._cache_key(board): {
                    "count": 5,
                    "at": "2026-01-01T00:00:00+00:00",
                    "url": board.url,
                    "tenant": board.tenant,
                    "connector": board.connector_id,
                }
            }
        ),
        encoding="utf-8",
    )

    fresh = build_seed._resume_live(
        build_seed._read_verified(),
        now=datetime(2026, 1, 5, tzinfo=UTC),
    )
    stale = build_seed._resume_live(
        build_seed._read_verified(),
        now=datetime(2026, 2, 1, tzinfo=UTC),
    )

    assert fresh == [(board, 5)]
    assert stale == []


@pytest.mark.asyncio
async def test_verify_all_skips_already_resumed_boards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build_seed, "VERIFIED_PATH", tmp_path / "verified.json")
    monkeypatch.setattr(build_seed, "LOG_PATH", tmp_path / "log.jsonl")
    board = Board("ats.greenhouse", "acme", "https://boards.greenhouse.io/acme")
    probed: list[Board] = []

    async def fake_verify(client: object, target: Board, sem: object) -> int:
        probed.append(target)
        return 1

    monkeypatch.setattr(build_seed, "verify_board", fake_verify)

    verified, outcomes = await build_seed.verify_all(
        None,
        {board: 1},
        already=[(board, 4)],  # type: ignore[arg-type]
    )

    assert probed == []
    assert verified == [(board, 4)]
    assert outcomes == {}


def test_legacy_verified_entries_take_the_cache_mtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "verified.json"
    path.write_text(json.dumps({"ats.greenhouse|acme": -1}), encoding="utf-8")
    stamp = datetime(2025, 1, 1, tzinfo=UTC).timestamp()
    os.utime(path, (stamp, stamp))
    monkeypatch.setattr(build_seed, "VERIFIED_PATH", path)

    entry = build_seed._read_verified()["ats.greenhouse|acme"]

    assert entry["count"] == -1
    assert str(entry["at"]).startswith("2025-01-01")
    # The window now starts at the last write instead of never expiring.
    assert build_seed._dead_is_current(entry, days=90, now=datetime(2025, 2, 1, tzinfo=UTC))
    assert not build_seed._dead_is_current(entry, days=90, now=datetime(2026, 1, 1, tzinfo=UTC))


def _fake_cdx(monkeypatch: pytest.MonkeyPatch, answer) -> None:
    """Stand in for ``_cdx_get``: ``answer(filters)`` returns a body or ``None``."""

    class _Response:
        def __init__(self, text: str) -> None:
            self.text = text
            self.content = text.encode()

    async def fake_get(client, params, purpose="", attempts=4):  # noqa: ANN001
        clauses = [value for key, value in params if key == "filter"]
        body = answer(clauses)
        return None if body is None else _Response(body)

    monkeypatch.setattr(build_seed, "_cdx_get", fake_get)


def test_bucket_children_refine_one_character() -> None:
    children = build_seed.bucket_children("f")

    assert len(children) == len(build_seed.BUCKETS)
    assert children[:2] == ("fa", "fb")
    assert children[-1] == "f9"


def test_bucket_is_complete_needs_the_bucket_or_every_part() -> None:
    done = {"fa"}

    assert build_seed.bucket_is_complete("fa", done)
    # One part is not the bucket: the rest of "f" is still unread.
    assert not build_seed.bucket_is_complete("f", done)
    assert not build_seed.bucket_is_complete("f", set())


def test_bucket_split_started_only_once_a_part_landed() -> None:
    assert build_seed.bucket_split_started("f", {"fa"})
    assert not build_seed.bucket_split_started("f", set())
    assert not build_seed.bucket_split_started("f", {"f"})


def test_a_refused_bucket_is_partitioned_and_its_parts_are_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / "subdomains.json"
    monkeypatch.setattr(build_seed, "SUBDOMAIN_CACHE_PATH", cache_path)
    monkeypatch.setattr(build_seed, "LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(build_seed, "REQUEST_GAP_S", 0.0)
    monkeypatch.setattr(build_seed, "BUCKET_PAUSE_S", 0.0)
    refused = "original:https?://a[a-z0-9-]*[.]bamboohr.com.*"
    landed = "original:https?://ab[a-z0-9-]*[.]bamboohr.com.*"

    def answer(clauses: list[str]) -> str | None:
        if clauses[0] == refused:
            return None
        if clauses[0] == landed:
            return json.dumps([["original"], ["https://abctest.bamboohr.com/careers"]])
        return json.dumps([["original"]])

    _fake_cdx(monkeypatch, answer)

    boards = asyncio.run(
        build_seed.harvest_initial_buckets(None, "ats.bamboohr", True, budget_s=600)  # type: ignore[arg-type]
    )

    assert "abctest" in {board.tenant for board in boards}
    buckets = json.loads(cache_path.read_text(encoding="utf-8"))["ats.bamboohr|buckets"]["buckets"]
    # The parts answered, so the refused bucket counts as read through them.
    assert "ab" in buckets
    assert "a" in buckets


def test_a_partly_split_bucket_resumes_without_reopening_the_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_path = tmp_path / "subdomains.json"
    cache_path.write_text(
        json.dumps({"ats.bamboohr|buckets": {"buckets": ["ab"], "boards": {}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(build_seed, "SUBDOMAIN_CACHE_PATH", cache_path)
    monkeypatch.setattr(build_seed, "LOG_PATH", tmp_path / "log.jsonl")
    monkeypatch.setattr(build_seed, "REQUEST_GAP_S", 0.0)
    monkeypatch.setattr(build_seed, "BUCKET_PAUSE_S", 0.0)
    refused = "original:https?://a[a-z0-9-]*[.]bamboohr.com.*"
    asked: list[str] = []

    def answer(clauses: list[str]) -> str | None:
        asked.append(clauses[0])
        return None if clauses[0] == refused else json.dumps([["original"]])

    _fake_cdx(monkeypatch, answer)

    asyncio.run(
        build_seed.harvest_initial_buckets(None, "ats.bamboohr", True, budget_s=600)  # type: ignore[arg-type]
    )

    # A bucket already split in an earlier pass is finished from its parts
    # instead of asking the index for the query it keeps refusing.
    assert refused not in asked


def test_dead_board_is_rebuilt_from_its_settled_identity() -> None:
    board = build_seed._dead_board("ats.workable|lawnstarter")

    assert board is not None
    assert board.url == "https://apply.workable.com/lawnstarter"
    assert build_seed._cache_key(board) == "ats.workable|lawnstarter"


def test_dead_boards_whose_key_drops_the_host_cannot_be_rebuilt() -> None:
    # Workday's identity drops the shard and UKG Pro's the employer code, so
    # neither key says which URL to ask; those wait for a harvest instead.
    assert build_seed._dead_board("ats.workday|alterra|Careers") is None
    assert build_seed._dead_board("ats.ultipro|8f14e45f-ceea-467f-a829-1558e1f0e1a0") is None


def test_recheck_dead_reopens_only_the_entries_it_can_address() -> None:
    checked = {
        "ats.workable|lawnstarter": {"count": -1, "at": "2026-11-22T00:00:00+00:00"},
        "ats.greenhouse|acme": {"count": 4, "at": "2026-11-22T00:00:00+00:00"},
        "ats.workday|alterra|Careers": {"count": -1, "at": "2026-11-22T00:00:00+00:00"},
    }

    boards, skipped = build_seed._dead_candidates(checked)

    assert [build_seed._cache_key(board) for board in boards] == ["ats.workable|lawnstarter"]
    assert skipped == {"ats.workday": 1}


def test_recheck_dead_can_be_narrowed_to_the_connector_that_measured_wrong() -> None:
    checked = {
        "ats.workable|lawnstarter": {"count": -1, "at": "2026-11-22T00:00:00+00:00"},
        "ats.bamboohr|ims": {"count": -1, "at": "2026-11-22T00:00:00+00:00"},
    }

    boards, _skipped = build_seed._dead_candidates(checked, {"ats.workable"})

    assert [build_seed._cache_key(board) for board in boards] == ["ats.workable|lawnstarter"]


def test_a_rechecked_dead_board_is_asked_again() -> None:
    board = build_seed._dead_board("ats.workable|lawnstarter")
    assert board is not None
    key = build_seed._cache_key(board)
    checked = {key: {"count": -1, "at": "2026-11-22T00:00:00+00:00"}}
    now = datetime(2026, 11, 26, tzinfo=UTC)

    # Inside the window it stays skipped: that is what hid the live board.
    assert _new_only({board: 1}, {}, checked, recheck_dead_days=90, now=now) == {}
    # ``--recheck-dead-after-days 0`` asks every settled-dead board again.
    assert _new_only({board: 1}, {}, checked, recheck_dead_days=0, now=now) == {board: 1}


async def test_taleo_count_comes_from_the_endpoint_its_page_uses() -> None:
    # A current Taleo section renders its listing client-side: the HTML links
    # no postings at all, and the page names the portal the JSON search wants.
    board = Board(
        "ats.taleo",
        "aa270",
        "https://aa270.taleo.net/careersection/ex/jobsearch.ftl?lang=en",
        "aa270|ex",
    )
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.method == "GET":
            return httpx.Response(200, text='queryString: "lang=en&portal=101430233"')
        return httpx.Response(
            200,
            json={"pagingData": {"totalCount": 88}, "requisitionList": [{"jobId": "1"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        count = await build_seed._verify_taleo(client, board, asyncio.Semaphore(1))

    assert count == 88
    assert calls[1][0] == "POST"
    assert "searchjobs?lang=en&portal=101430233" in calls[1][1]


async def test_a_legacy_taleo_section_still_counts_its_job_links() -> None:
    board = Board(
        "ats.taleo",
        "acme",
        "https://acme.taleo.net/careersection/2/jobsearch.ftl?lang=en",
        "acme|2",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='<a href="/careersection/2/jobdetail.ftl?job=7">A</a>'
            '<a href="/careersection/2/jobdetail.ftl?job=8">B</a>',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        count = await build_seed._verify_taleo(client, board, asyncio.Semaphore(1))

    assert count == 2
