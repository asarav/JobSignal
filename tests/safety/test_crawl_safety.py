"""Crawl safety suite (spec §19.5 requirements 5, 6 and 8).

These tests are CI-blocking. They cover the crawler half of the safety
requirements: a challenge page is recorded and abandoned without a retry, an
auth boundary is reported without trying credentials or altered headers,
``robots.txt`` exclusions are honoured before the URL is requested, and no
request is sent to the local machine, the local network or a non-http scheme.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from app.application.config import HttpConfig
from app.domain.models.common import Err, Ok
from app.domain.models.http import HttpRequest, HttpResponse
from app.domain.services.url_guard import unsafe_url_reason
from app.infrastructure.http.httpx_client import HttpxClient
from app.infrastructure.http.policy_client import PolicyHttpClient, RateLimiter
from tests.support.fakes import FakeHttpClient

ROOT = Path(__file__).resolve().parents[2]

#: A challenge page and a plausible-looking public board.
CHALLENGE_URL = "https://boards.example/apply"
ROBOTS_URL = "https://boards.example/robots.txt"


def _response(url: str, status: int, body: bytes = b"", **headers: str) -> HttpResponse:
    return HttpResponse(
        status=status, url=url, headers=dict(headers), body=body, elapsed_ms=1
    )


def _client(
    responses: dict[str, HttpResponse] | None = None, **kwargs: object
) -> tuple[PolicyHttpClient, FakeHttpClient]:
    transport = FakeHttpClient(responses=responses)
    policy = PolicyHttpClient(
        transport=transport,
        # No waiting between requests, and no shared origin state.
        rate_limiter=RateLimiter(min_interval_s=0.0, burst=100),
        **kwargs,  # type: ignore[arg-type]
    )
    return policy, transport


# --- §19.5.8: SSRF ---------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/apply",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "http://localhost:3000/",
        "http://[::1]/",
        "http://10.0.0.5/",
        "http://192.168.1.10/",
        "http://0.0.0.0/",
    ],
)
@pytest.mark.asyncio
async def test_private_local_and_non_http_urls_are_never_requested(url):
    policy, transport = _client()

    result = await policy.send(HttpRequest(method="GET", url=url))

    assert isinstance(result, Err)
    assert result.error.code == "crawl.unsafe_url"
    assert result.error.retryable is False
    # Not even the robots lookup for that host happened.
    assert transport.sent == []


@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",  # 127.0.0.1 in decimal
        "http://0x7f000001/",  # and in hex
        "http://localhost./",  # trailing dot
        "http://metadata.google.internal/computeMetadata/v1/",
    ],
)
def test_aliases_of_the_local_machine_are_refused(url):
    assert unsafe_url_reason(url) is not None


def test_a_public_url_is_accepted():
    assert unsafe_url_reason("https://jobs.lever.co/acme/123") is None


@pytest.mark.asyncio
async def test_a_fixture_origin_is_reachable_only_with_the_explicit_override():
    url = "http://127.0.0.1:8123/apply"

    blocked, blocked_transport = _client()
    assert isinstance(await blocked.send(HttpRequest(method="GET", url=url)), Err)
    assert blocked_transport.sent == []

    allowed, allowed_transport = _client(allow_private_hosts=True)
    result = await allowed.send(HttpRequest(method="GET", url=url))
    assert isinstance(result, Ok)
    assert url in [str(req.url) for req in allowed_transport.sent]


@pytest.mark.asyncio
async def test_a_redirect_into_the_local_network_is_refused():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "http://169.254.169.254/latest/meta-data/"}
        )

    client = HttpxClient(HttpConfig(), transport=httpx.MockTransport(handler))
    result = await client.send(HttpRequest(method="GET", url="https://boards.example/apply"))

    assert isinstance(result, Err)
    assert result.error.code == "network.unsafe_redirect"
    assert result.error.retryable is False


@pytest.mark.asyncio
async def test_a_public_redirect_is_followed_and_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/apply":
            return httpx.Response(301, headers={"location": "/careers/apply"})
        return httpx.Response(200, content=b"<html>ok</html>")

    client = HttpxClient(HttpConfig(), transport=httpx.MockTransport(handler))
    result = await client.send(HttpRequest(method="GET", url="https://boards.example/apply"))

    assert isinstance(result, Ok)
    assert result.value.status == 200
    assert result.value.url == "https://boards.example/careers/apply"


@pytest.mark.asyncio
async def test_the_redirect_cap_is_enforced():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/next"})

    client = HttpxClient(HttpConfig(max_redirects=5), transport=httpx.MockTransport(handler))
    result = await client.send(HttpRequest(method="GET", url="https://boards.example/apply"))

    assert isinstance(result, Err)
    assert result.error.code == "http.redirect_loop"


@pytest.mark.asyncio
async def test_the_diagnostics_client_refuses_a_loopback_address():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("the request must never be sent")

    client = HttpxClient(HttpConfig(), transport=httpx.MockTransport(handler))
    result = await client.send(
        HttpRequest(method="GET", url="http://127.0.0.1:8181/status")
    )

    assert isinstance(result, Err)
    assert result.error.code == "validation.invalid_params"


# --- §19.5.5: no CAPTCHA bypass -------------------------------------------


@pytest.mark.asyncio
async def test_a_challenge_page_is_recorded_and_not_retried():
    responses = {
        CHALLENGE_URL: _response(
            CHALLENGE_URL, 403, b"<html>Please verify you are human</html>"
        )
    }
    policy, transport = _client(responses)

    result = await policy.send(HttpRequest(method="GET", url=CHALLENGE_URL))

    assert isinstance(result, Err)
    assert result.error.code == "blocked"
    assert result.error.retryable is False
    # One robots lookup and one page request: no retries, no solver attempt.
    assert [str(req.url) for req in transport.sent] == [ROBOTS_URL, CHALLENGE_URL]


SOLVER_LIBRARIES = (
    "2captcha",
    "anticaptcha",
    "capsolver",
    "cloudscraper",
    "undetected-chromedriver",
    "stealth",
    "puppeteer",
)


def test_no_captcha_solver_library_is_a_dependency():
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8").lower()
    offenders = [library for library in SOLVER_LIBRARIES if library in text]
    assert offenders == [], f"solver libraries in pyproject.toml: {offenders}"


def test_production_code_carries_no_solver_import():
    offenders: list[str] = []
    for path in (ROOT / "app").rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for library in SOLVER_LIBRARIES:
            pattern = re.compile(rf"^\s*(?:from|import)\s+{re.escape(library)}\b", re.M)
            if pattern.search(text):
                offenders.append(f"{path.relative_to(ROOT)}: {library}")
    assert offenders == [], offenders


# --- §19.5.6: no access-control circumvention -----------------------------


@pytest.mark.asyncio
async def test_a_plain_401_is_an_auth_boundary_not_a_retry():
    responses = {CHALLENGE_URL: _response(CHALLENGE_URL, 401)}
    policy, transport = _client(responses)

    result = await policy.send(HttpRequest(method="GET", url=CHALLENGE_URL))

    assert isinstance(result, Err)
    assert result.error.code == "auth.required"
    assert result.error.retryable is False
    sent_to_page = [req for req in transport.sent if str(req.url) == CHALLENGE_URL]
    assert len(sent_to_page) == 1
    # No credential was guessed and no header was changed for a second try.
    for request in transport.sent:
        header_names = {name.lower() for name in request.headers}
        assert "authorization" not in header_names
        assert "cookie" not in header_names


@pytest.mark.asyncio
async def test_a_plain_403_is_an_auth_boundary():
    """Only a challenge *page* is ``blocked``; a bare 403 is access control."""
    responses = {CHALLENGE_URL: _response(CHALLENGE_URL, 403, b"forbidden")}
    policy, transport = _client(responses)

    result = await policy.send(HttpRequest(method="GET", url=CHALLENGE_URL))

    assert isinstance(result, Err)
    assert result.error.code == "auth.required"
    assert len([req for req in transport.sent if str(req.url) == CHALLENGE_URL]) == 1


@pytest.mark.asyncio
async def test_a_login_redirect_is_reported_not_followed():
    responses = {
        CHALLENGE_URL: _response(CHALLENGE_URL, 302, b"", location="/login?next=/apply")
    }
    policy, transport = _client(responses)

    result = await policy.send(HttpRequest(method="GET", url=CHALLENGE_URL))

    assert isinstance(result, Err)
    assert result.error.code == "auth.required"
    assert "/login" not in [str(req.url) for req in transport.sent]


@pytest.mark.asyncio
async def test_a_robots_disallowed_url_is_never_fetched():
    responses = {
        ROBOTS_URL: _response(ROBOTS_URL, 200, b"User-agent: *\nDisallow: /private")
    }
    policy, transport = _client(responses)

    result = await policy.send(
        HttpRequest(method="GET", url="https://boards.example/private/apply")
    )

    assert isinstance(result, Err)
    assert result.error.code == "robots.disallowed"
    assert [str(req.url) for req in transport.sent] == [ROBOTS_URL]
