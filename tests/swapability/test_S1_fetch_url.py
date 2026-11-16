"""S1: FetchUrl with FakeHttpClient + socket-blocking fixture (no real crawl)."""

import pytest
from app.application.commands.diagnostics import FetchUrlUseCase
from app.application.dto import FetchUrlParams
from app.domain.models.common import PortError
from app.domain.models.http import HttpResponse
from tests.support.containers import build_test_container
from tests.support.fakes import FakeHttpClient


@pytest.mark.asyncio
async def test_fetch_url_returns_status():
    http = FakeHttpClient(
        {
            "https://example.com": HttpResponse(
                status=200,
                url="https://example.com",
                headers={},
                body=b"hi",
                elapsed_ms=5,
            )
        }
    )
    container = build_test_container(http=http)
    use_case = FetchUrlUseCase(container)

    result = await use_case.execute(FetchUrlParams(url="https://example.com"))

    assert result.status == 200
    assert result.bytes == 2
    assert result.url == "https://example.com"
    assert [str(req.url) for req in http.sent] == ["https://example.com"]


@pytest.mark.asyncio
async def test_fetch_url_socket_blocked_no_real_crawl():
    http = FakeHttpClient()
    http.block_socket = True
    use_case = FetchUrlUseCase(build_test_container(http=http))

    with pytest.raises(PortError) as exc:
        await use_case.execute(FetchUrlParams(url="https://example.com"))

    assert exc.value.code == "network.offline"
    assert http.sent == []


@pytest.mark.asyncio
async def test_fetch_url_rejects_non_http_scheme():
    http = FakeHttpClient()
    use_case = FetchUrlUseCase(build_test_container(http=http))

    with pytest.raises(PortError) as exc:
        await use_case.execute(FetchUrlParams(url="file:///etc/passwd"))

    assert exc.value.code == "validation.invalid_params"
    assert http.sent == []
