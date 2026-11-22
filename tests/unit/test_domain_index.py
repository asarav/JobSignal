"""The domain-index provider reconstructs boards the fixed-host rotation cannot."""

from __future__ import annotations

import json

import pytest
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.discovery import DiscoveryQuery
from app.domain.models.http import HttpResponse
from app.plugins.discovery.domain_index import (
    CDX_LIMIT,
    Candidate,
    DomainIndexProvider,
    IndexTarget,
    _candidate,
    _cdx_url,
)
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger


def _ctx(http: FakeHttpClient) -> ExecutionContext:
    return ExecutionContext(
        task_id="domain-index", clock=FakeClock(), http=http, logger=FakeStructuredLogger(),
        cancel=CancellationToken(), deadline=None, config={},
    )


def test_workday_candidate_carries_locale_and_site():
    candidate = _candidate(
        "ats.workday",
        "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers/job/Berlin/Engineer_R1",
    )
    assert candidate == Candidate(
        "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers",
        "acme",
        "Acme",
        "ats.workday",
        tenant_key="acme",
        dedupe_key="workday:acme:acmecareers",
    )
    assert _candidate(
        "ats.workday", "https://acme.myworkdayjobs.com/Careers/job/1"
    ) == Candidate(
        "https://acme.myworkdayjobs.com/Careers",
        "acme",
        "Acme",
        "ats.workday",
        tenant_key="acme",
        dedupe_key="workday:acme:careers",
    )
    # A bare board host has no site, so it cannot be crawled.
    assert _candidate("ats.workday", "https://acme.wd5.myworkdayjobs.com/") is None
    # Indexed asset and API paths are not career sites.
    assert _candidate("ats.workday", "https://abc.wd1.myworkdayjobs.com/favicon.ico") is None
    assert _candidate(
        "ats.workday", "https://abc.wd1.myworkdayjobs.com/wday/cxs/abc/site/jobs"
    ) is None


def test_subdomain_boards():
    assert _candidate("ats.icims", "https://careers-acme.icims.com/jobs/1/job") == Candidate(
        "https://careers-acme.icims.com", "careers-acme", "Careers Acme", "ats.icims"
    )
    assert _candidate("ats.icims", "https://www.icims.com/jobs") is None
    assert _candidate("ats.bamboohr", "https://a1comics.bamboohr.com/careers") == Candidate(
        "https://a1comics.bamboohr.com/careers", "a1comics", "A1Comics", "ats.bamboohr"
    )
    assert _candidate("ats.bamboohr", "https://www.bamboohr.com") is None
    assert _candidate("ats.eightfold", "https://acme.eightfold.ai/careers") == Candidate(
        "https://acme.eightfold.ai/careers", "acme", "Acme", "ats.eightfold"
    )
    assert _candidate("ats.eightfold", "https://cdn.eightfold.ai/assets/logo.js") is None
    assert _candidate("ats.eightfold", "https://www.eightfold.ai") is None


def test_path_boards():
    board = "6b442184-52a5-4635-8db5-5aa4bed2a563"
    candidate = _candidate(
        "ats.ultipro",
        f"https://recruiting.ultipro.com/acme01/JobBoard/{board}/OpportunityDetail?id=1",
    )
    assert candidate == Candidate(
        f"https://recruiting.ultipro.com/ACME01/JobBoard/{board}",
        "ACME01",
        None,
        "ats.ultipro",
        tenant_key=board,
    )
    assert _candidate(
        "ats.workable", "https://apply.workable.com/huggingface/j/ABC123/"
    ) == Candidate(
        "https://apply.workable.com/huggingface",
        "huggingface",
        "Huggingface",
        "ats.workable",
    )
    assert _candidate("ats.workable", "https://apply.workable.com/api/v1/widget") is None


def test_rotation_walks_new_slices_each_pass():
    provider = DomainIndexProvider()
    first = provider.slices_for(0, 12)
    second = provider.slices_for(12, 12)
    assert [bucket for _target, bucket in first] == ["a", "b", "c"]
    assert [bucket for _target, bucket in second] == ["d", "e", "f"]
    assert all(target.connector_id == "ats.workday" for target, _bucket in first)
    assert provider.slices_for(0, 12) == provider.slices_for(0, 12)


@pytest.mark.asyncio
async def test_discover_yields_reconstructed_boards():
    target = IndexTarget("myworkdayjobs.com", "ats.workday", "domain")
    provider = DomainIndexProvider(targets=(target,), slices_per_pass=1)
    url = _cdx_url(target, "a", CDX_LIMIT)
    body = json.dumps(
        [
            ["original"],
            ["https://acme.wd5.myworkdayjobs.com/AcmeCareers"],
            ["https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers/job/1"],
            ["https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers/assets/logo"],
            ["https://acme.wd5.myworkdayjobs.com/"],
        ]
    ).encode()
    http = FakeHttpClient(
        {
            url: HttpResponse(
                status=200, url=url, headers={"content-type": "application/json"},
                body=body, elapsed_ms=5,
            )
        }
    )

    items = [
        source
        async for source in provider.discover(
            DiscoveryQuery(extra={"cursor": 0, "slices": 12}), _ctx(http)
        )
    ]

    assert len(items) == 1
    assert items[0].url == "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers"
    assert items[0].ats_hint == "ats.workday"
    assert items[0].tenant_key == "acme"


@pytest.mark.asyncio
async def test_a_throttled_slice_returns_nothing_instead_of_raising():
    provider = DomainIndexProvider()
    http = FakeHttpClient()
    http.block_socket = True

    items = [
        source
        async for source in provider.discover(
            DiscoveryQuery(extra={"cursor": 0, "slices": 12}), _ctx(http)
        )
    ]

    assert items == []
