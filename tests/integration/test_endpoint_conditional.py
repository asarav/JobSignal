"""Conditional requests on the endpoint read path (M11)."""

from __future__ import annotations

from typing import Any

import pytest
from app.application.endpoint_verifier import EndpointVerifier
from app.domain.models.common import Ok
from app.domain.models.http import HttpResponse
from tests.support.containers import build_test_container


class FakeHttp:
    def __init__(self, responses: list[HttpResponse]) -> None:
        self._responses = responses
        self.requests: list[Any] = []

    async def send(self, req):
        self.requests.append(req)
        return Ok(self._responses.pop(0))


def _response(
    status: int, *, headers: dict[str, str] | None = None, body: bytes = b""
) -> HttpResponse:
    return HttpResponse(
        status=status,
        url="https://acme.test/jobs/1",
        headers=headers or {},
        body=body,
        elapsed_ms=5,
    )


def _container_with_job():
    container = build_test_container()
    container.uow_factory.jobs["job_1"] = {
        "id": "job_1",
        "company_id": "co_a",
        "source_id": "src_1",
        "title": "Data Engineer",
        "posting_status": "active",
        "canonical_url": "https://acme.test/jobs/1",
        "first_seen_at": "2026-01-01T00:00:00Z",
        "fetched_at": "2026-01-01T00:00:00Z",
    }
    return container


@pytest.mark.asyncio
async def test_an_etag_is_revalidated_and_a_304_keeps_the_previous_state() -> None:
    container = _container_with_job()
    http = FakeHttp(
        [
            _response(
                200,
                headers={"ETag": '"v1"', "Last-Modified": "Wed, 01 Jan 2026 00:00:00 GMT"},
                body=b"<html>Apply now</html>",
            ),
            _response(304, headers={"ETag": '"v1"'}),
        ]
    )
    verifier = EndpointVerifier(container, http=http)

    first = await verifier.verify(job_ids=["job_1"], force=True)
    second = await verifier.verify(job_ids=["job_1"], force=True)

    assert first["checked"] == 1 and second["checked"] == 1
    assert http.requests[0].headers == {}
    assert http.requests[1].headers == {"If-None-Match": '"v1"'}
    assert "If-Modified-Since" not in http.requests[1].headers

    async with container.uow_factory() as uow:
        observations = list(await uow.intelligence.endpoints.list_for_job("job_1"))
        latest = await uow.intelligence.endpoints.latest("job_1")
    assert latest is not None
    assert latest["http_status"] == 304
    assert latest["details"]["conditional"] == "not_modified"
    assert latest["details"]["reused_state"] == observations[0]["state"]


@pytest.mark.asyncio
async def test_last_modified_is_used_when_there_is_no_etag() -> None:
    container = _container_with_job()
    http = FakeHttp(
        [
            _response(
                200,
                headers={"Last-Modified": "Wed, 01 Jan 2026 00:00:00 GMT"},
                body=b"<html>Apply now</html>",
            ),
            _response(304),
        ]
    )
    verifier = EndpointVerifier(container, http=http)

    await verifier.verify(job_ids=["job_1"], force=True)
    await verifier.verify(job_ids=["job_1"], force=True)

    assert http.requests[1].headers == {
        "If-Modified-Since": "Wed, 01 Jan 2026 00:00:00 GMT"
    }
