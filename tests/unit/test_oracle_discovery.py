"""ORC discovery reconstructs a Candidate Experience board from pod index rows."""

from __future__ import annotations

import json

import pytest
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.discovery import DiscoveryQuery
from app.domain.models.http import HttpResponse
from app.plugins.discovery.oracle_recruiting import (
    OracleRecruitingProvider,
    cdx_url,
    orc_board,
)
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger


def _ctx(http: FakeHttpClient) -> ExecutionContext:
    return ExecutionContext(
        task_id="orc-discovery", clock=FakeClock(), http=http, logger=FakeStructuredLogger(),
        cancel=CancellationToken(), deadline=None, config={},
    )


def test_board_from_localised_and_bare_urls() -> None:
    board = orc_board(
        "https://cbcz.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/2180"
    )
    assert board is not None
    assert board.url == "https://cbcz.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX"
    assert board.site == "CX"
    assert board.key == "cbcz.fa.us2.oraclecloud.com/CX"

    bare = orc_board(
        "https://acme.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/sites/CX_1001"
    )
    assert bare is not None
    assert bare.url.endswith("/en/sites/CX_1001")

    assert orc_board(
        "https://admin.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX"
    ) is None
    assert orc_board(
        "https://acme.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/assets/logo"
    ) is not None  # board root is the site, not the asset
    assert orc_board("https://acme.oraclecloud.com/hcmUI/CandidateExperience") is None
    assert orc_board("https://acme.fa.us2.oraclecloud.com/ords/f?p=1") is None


def test_pods_rotate_with_the_pass() -> None:
    provider = OracleRecruitingProvider(
        pods=("a.fa.us2.oraclecloud.com", "b.fa.us2.oraclecloud.com")
    )
    assert provider.pods_for(0, 12) == [
        "a.fa.us2.oraclecloud.com",
        "b.fa.us2.oraclecloud.com",
    ]
    assert provider.pods_for(12, 12) == [
        "a.fa.us2.oraclecloud.com",
        "b.fa.us2.oraclecloud.com",
    ]


@pytest.mark.asyncio
async def test_discover_yields_orc_sites() -> None:
    pod = "fa.us2.oraclecloud.com"
    provider = OracleRecruitingProvider(
        pods=(pod,), pods_per_pass=1, cdx_limit=50
    )
    url = cdx_url(pod, 50)
    body = json.dumps(
        [
            ["original"],
            [f"https://cbcz.{pod}/hcmUI/CandidateExperience/en/sites/CX/job/1"],
            [f"https://cbcz.{pod}/hcmUI/CandidateExperience/en/sites/CX/job/2"],
            [f"https://other.{pod}/hcmUI/CandidateExperience/en/sites/CX_9"],
            [f"https://cbcz.{pod}/hcmUI/CandidateExperience/"],
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

    assert len(items) == 2
    assert items[0].ats_hint == "ats.oracle_recruiting"
    assert items[0].tenant_key == "CX"
    assert items[0].url == (
        f"https://cbcz.{pod}/hcmUI/CandidateExperience/en/sites/CX"
    )


@pytest.mark.asyncio
async def test_failed_pod_yields_nothing() -> None:
    provider = OracleRecruitingProvider(pods=("fa.us2.oraclecloud.com",))
    http = FakeHttpClient()
    http.block_socket = True
    items = [
        source
        async for source in provider.discover(
            DiscoveryQuery(extra={"cursor": 0, "slices": 12}), _ctx(http)
        )
    ]
    assert items == []
