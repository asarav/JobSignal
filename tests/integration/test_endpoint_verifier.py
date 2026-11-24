"""The polite verifier records evidence, never submits, and respects its budget."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from app.application.endpoint_verifier import EndpointVerifier
from app.domain.models.common import Err, Ok, PortError
from app.domain.models.http import HttpRequest, HttpResponse
from tests.support.containers import build_test_container

NOW = datetime(2026, 3, 1, tzinfo=UTC)


class _FakeHttp:
    def __init__(
        self, *, status: int = 200, body: str = "", error: PortError | None = None
    ) -> None:
        self.status = status
        self.body = body
        self.error = error
        self.requests: list[HttpRequest] = []

    async def send(self, req: HttpRequest) -> Any:
        self.requests.append(req)
        if self.error is not None:
            return Err(self.error)
        return Ok(
            HttpResponse(
                status=self.status,
                url=req.url,
                headers={},
                body=self.body.encode("utf-8"),
                elapsed_ms=5,
            )
        )


def _seed(container) -> None:
    container.uow_factory.jobs["job_1"] = {
        "id": "job_1",
        "company_id": "co_1",
        "source_id": "src_1",
        "title": "Data Engineer",
        "posting_status": "active",
        "canonical_url": "https://acme.test/jobs/1",
        "description_text": "Own ingestion.",
    }


@pytest.mark.asyncio
async def test_records_a_closed_page_without_touching_any_control() -> None:
    http = _FakeHttp(
        status=200,
        body="<html><body><p>This position has been filled.</p></body></html>",
    )
    container = build_test_container(http=http)
    _seed(container)

    result = await EndpointVerifier(container).verify(job_ids=["job_1"], force=True)

    assert result["checked"] == 1
    assert result["states"] == {"closed_message": 1}
    assert [request.method for request in http.requests] == ["GET"]
    assert all(request.json_body is None for request in http.requests)

    async with container.uow_factory() as uow:
        latest = await uow.intelligence.endpoints.latest("job_1")
    assert latest is not None
    assert latest["state"] == "closed_message"
    assert latest["http_status"] == 200


@pytest.mark.asyncio
async def test_a_timeout_is_recorded_as_transient_not_as_closed() -> None:
    http = _FakeHttp(error=PortError(code="http.timeout", message="timed out"))
    container = build_test_container(http=http)
    _seed(container)

    result = await EndpointVerifier(container).verify(job_ids=["job_1"], force=True)

    assert result["states"] == {"timeout": 1}
    async with container.uow_factory() as uow:
        latest = await uow.intelligence.endpoints.latest("job_1")
    assert latest is not None and latest["state"] == "timeout"


@pytest.mark.asyncio
async def test_the_budget_skips_a_recently_checked_job() -> None:
    http = _FakeHttp(status=200, body="<html><body>Apply now</body></html>")
    container = build_test_container(http=http)
    _seed(container)
    verifier = EndpointVerifier(container)

    await verifier.verify(job_ids=["job_1"], force=True)
    skipped = await verifier.verify(limit=10)

    assert skipped["checked"] == 0
    assert len(http.requests) == 1
