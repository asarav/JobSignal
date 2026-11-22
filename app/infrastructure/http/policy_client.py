"""Policy-aware HTTP client: robots, rate limits, retries, caps.

Wraps a raw :class:`HttpClient` (the transport) and enforces the crawl policy
defined by ``AppConfig.http`` / ``AppConfig.crawl`` and §6.6 of the spec.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.http import HttpRequest, HttpResponse
from app.domain.ports.http import HttpClient
from app.domain.services.url_guard import unsafe_url_reason

#: Markers that indicate a challenge / anti-bot page.
_CHALLENGE_MARKERS = (
    "captcha",
    "verify you are human",
    "cf-challenge",
    "challenge-platform",
    "access denied",
    "just a moment",
    "enable javascript",
    "bot detection",
)
_LOGIN_MARKERS = ("login", "signin", "sign-in", "auth", "sso")

_RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}
_AUTH_STATUSES = {401, 403}


@dataclass
class _RobotsRules:
    allowed: bool = True  # False means temp-deny (unreachable)
    crawl_delay: float | None = None
    disallow: list[str] = field(default_factory=list)
    allow: list[str] = field(default_factory=list)
    expires_at: float = 0.0
    backoff_until: float = 0.0


@dataclass
class _OriginState:
    tokens: float = 2.0
    last_refill: float = 0.0
    in_flight: int = 0
    backoff_until: float = 0.0
    robots: _RobotsRules | None = None


def _parse_robots(text: str, our_ua: str) -> _RobotsRules:
    rules = _RobotsRules()
    # Group parsing: collect rules per user-agent group.
    groups: dict[str, list[tuple[str, str]]] = {}
    current: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        field_name, _, value = line.partition(":")
        field_name = field_name.strip().lower()
        value = value.strip()
        if field_name == "user-agent":
            current = [value.lower()]
            groups.setdefault(value.lower(), [])
        elif current:
            for ua in current:
                groups.setdefault(ua, []).append((field_name, value))
    # Pick the group: exact UA match, else "*".
    chosen: list[tuple[str, str]] = []
    ua_lower = our_ua.lower()
    if ua_lower in groups:
        chosen = groups[ua_lower]
    elif "*" in groups:
        chosen = groups["*"]
    for field_name, value in chosen:
        if field_name == "disallow":
            if value:
                rules.disallow.append(value)
        elif field_name == "allow":
            if value:
                rules.allow.append(value)
        elif field_name == "crawl-delay":
            try:
                rules.crawl_delay = float(value)
            except ValueError:
                pass
    return rules


def _robots_allows(rules: _RobotsRules, path: str) -> bool:
    if rules.backoff_until > time.monotonic():
        return False
    if not rules.allowed:
        return False
    # Longest match wins.
    best_allow = -1
    best_disallow = -1
    for pat in rules.allow:
        if path.startswith(pat.rstrip("*")):
            best_allow = max(best_allow, len(pat))
    for pat in rules.disallow:
        if path.startswith(pat.rstrip("*")):
            best_disallow = max(best_disallow, len(pat))
    if best_disallow == -1:
        return True
    if best_allow > best_disallow:
        return True
    return False


@dataclass
class RateLimiter:
    """Per-origin token bucket + concurrency gate."""

    min_interval_s: float = 1.0
    burst: int = 2
    max_concurrent: int = 2
    _state: dict[str, _OriginState] = field(default_factory=dict)

    def _origin(self, host: str) -> _OriginState:
        st = self._state.get(host)
        if st is None:
            st = _OriginState(tokens=float(self.burst), last_refill=time.monotonic())
            self._state[host] = st
        return st

    async def acquire(self, host: str) -> None:
        st = self._origin(host)
        # Wait out backoff.
        now = time.monotonic()
        if st.backoff_until > now:
            await asyncio.sleep(st.backoff_until - now)
        # Concurrency gate.
        while st.in_flight >= self.max_concurrent:
            await asyncio.sleep(0.05)
            now = time.monotonic()
        # Token bucket refill.
        now = time.monotonic()
        elapsed = now - st.last_refill
        st.tokens = min(
            float(self.burst), st.tokens + elapsed * (1.0 / max(self.min_interval_s, 1e-6))
        )
        st.last_refill = now
        if st.tokens < 1.0:
            wait = (1.0 - st.tokens) * self.min_interval_s
            await asyncio.sleep(wait)
            now = time.monotonic()
            st.tokens = min(
                float(self.burst), st.tokens + wait * (1.0 / max(self.min_interval_s, 1e-6))
            )
            st.last_refill = now
        st.tokens -= 1.0
        st.in_flight += 1

    def release(self, host: str) -> None:
        st = self._state.get(host)
        if st is not None and st.in_flight > 0:
            st.in_flight -= 1

    def backoff(self, host: str, seconds: float) -> None:
        st = self._origin(host)
        st.backoff_until = max(st.backoff_until, time.monotonic() + seconds)


@dataclass
class PolicyHttpClient:
    transport: HttpClient
    rate_limiter: RateLimiter = field(default_factory=RateLimiter)
    user_agent: str = "Lodestar/0.0.0 (+local-desktop)"
    max_retries: int = 4
    base_backoff_s: float = 2.0
    cap_backoff_s: float = 300.0
    timeout_s_default: float = 20.0
    max_timeout_s: float = 120.0
    max_bytes: int = 5_000_000
    max_redirects: int = 5
    #: Test-only escape hatch for fixture servers. Nothing in the settings or
    #: the worker turns it on: the app never fetches loopback, private or
    #: link-local addresses (§16, §19.5.8).
    allow_private_hosts: bool = False
    robots_ttl_s: float = 24 * 3600.0
    _robots_cache: dict[str, _RobotsRules] = field(default_factory=dict)
    _inflight: dict[str, asyncio.Future] = field(default_factory=dict)

    async def send(self, req: HttpRequest) -> Result[HttpResponse, PortError]:
        host = urlparse(req.url).netloc
        # In-flight coalescing by URL.
        loop = asyncio.get_event_loop()
        fut = self._inflight.get(req.url)
        if fut is not None and not fut.done():
            try:
                return await fut
            except Exception:  # noqa: BLE001
                pass

        future = loop.create_future()
        self._inflight[req.url] = future
        try:
            result = await self._send_once(req, host)
            future.set_result(result)
            return result
        except Exception as exc:  # noqa: BLE001
            future.set_exception(exc)
            raise
        finally:
            self._inflight.pop(req.url, None)

    async def _send_once(self, req: HttpRequest, host: str) -> Result[HttpResponse, PortError]:
        # Never send anything to the machine itself or the local network. This
        # runs before the robots fetch so a blocked URL cannot even probe the
        # origin.
        if not self.allow_private_hosts:
            reason = unsafe_url_reason(req.url)
            if reason is not None:
                return Err(
                    PortError(
                        code="crawl.unsafe_url",
                        message=f"refusing to fetch {req.url}: {reason}",
                        retryable=False,
                    )
                )

        # Robots check.
        if req.respect_robots:
            allowed, crawl_delay = await self._robots_allowed(host, req.url)
            if not allowed:
                return Err(
                    PortError(
                        code="robots.disallowed",
                        message=f"robots.txt disallows {req.url}",
                        retryable=False,
                    )
                )
            # Apply crawl-delay to rate limiter if larger.
            if crawl_delay is not None and crawl_delay > self.rate_limiter.min_interval_s:
                self.rate_limiter.min_interval_s = crawl_delay

        await self.rate_limiter.acquire(host)
        try:
            timeout = min(max(req.timeout_s, 0.1), self.max_timeout_s)
            headers = dict(req.headers)
            headers.setdefault("User-Agent", self.user_agent)

            # Conditional request headers pass-through (caller may set them).
            last_error: PortError | None = None
            for attempt in range(self.max_retries):
                result = await self.transport.send(
                    HttpRequest(
                        method=req.method,
                        url=req.url,
                        headers=headers,
                        json_body=req.json_body,
                        timeout_s=timeout,
                        max_bytes=min(req.max_bytes, self.max_bytes),
                        respect_robots=req.respect_robots,
                    )
                )
                if isinstance(result, Err):
                    err = result.error
                    if err.code == "http.redirect_loop":
                        return result
                    # Auth boundary.
                    if err.code in ("http.unauthorized", "http.forbidden"):
                        self.rate_limiter.backoff(host, 0)
                        return Err(
                            PortError(code="auth.required", message=err.message, retryable=False)
                        )
                    last_error = err
                    if not err.retryable:
                        self.rate_limiter.release(host)
                        return result
                    # retry
                    await self._sleep_backoff(attempt, None)
                    continue

                resp = result.value
                # Size cap.
                if len(resp.body) > self.max_bytes:
                    self.rate_limiter.release(host)
                    return Err(
                        PortError(
                            code="http.too_large",
                            message="response exceeds max_bytes",
                            retryable=False,
                        )
                    )

                # Anti-bot challenge detection.
                if resp.status in _AUTH_STATUSES:
                    body_lower = resp.body[:4096].decode("utf-8", "ignore").lower()
                    if any(m in body_lower for m in _CHALLENGE_MARKERS):
                        # A challenge page is "blocked": the origin is backed
                        # off and the run makes no further attempts (§19.5.5).
                        self.rate_limiter.backoff(host, 6 * 3600)
                        self.rate_limiter.release(host)
                        return Err(
                            PortError(
                                code="blocked", message="challenge page detected", retryable=False
                            )
                        )
                    # A plain 401/403 is an auth boundary, not a challenge:
                    # report it without guessing a credential or changing
                    # headers (§19.5.6).
                    self.rate_limiter.release(host)
                    return Err(
                        PortError(
                            code="auth.required", message=f"HTTP {resp.status}", retryable=False
                        )
                    )

                # Login redirect detection.
                if resp.status in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location", "") or resp.headers.get("Location", "")
                    if any(m in loc.lower() for m in _LOGIN_MARKERS):
                        self.rate_limiter.release(host)
                        return Err(
                            PortError(
                                code="auth.required", message="login redirect", retryable=False
                            )
                        )

                # Retryable status.
                if resp.status in _RETRYABLE_STATUSES:
                    retry_after = self._retry_after(resp)
                    if attempt < self.max_retries - 1:
                        await self._sleep_backoff(attempt, retry_after)
                        last_error = PortError(
                            code="http.server_error", message=f"HTTP {resp.status}", retryable=True
                        )
                        continue
                    self.rate_limiter.release(host)
                    return Err(
                        PortError(
                            code="http.server_error", message=f"HTTP {resp.status}", retryable=True
                        )
                    )

                # 404 tenant etc. pass through.
                self.rate_limiter.release(host)
                return Ok(resp)

            self.rate_limiter.release(host)
            if last_error is None:
                last_error = PortError(
                    code="http.failed", message="request failed", retryable=False
                )
            return Err(last_error)
        except Exception as exc:  # noqa: BLE001
            self.rate_limiter.release(host)
            return Err(PortError(code="http.transport", message=str(exc), retryable=True))

    async def _sleep_backoff(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is not None:
            await asyncio.sleep(min(retry_after, self.cap_backoff_s))
            return
        delay = min(self.cap_backoff_s, self.base_backoff_s * (2**attempt))
        # full jitter
        import random

        await asyncio.sleep(random.uniform(0, delay))

    def _retry_after(self, resp: HttpResponse) -> float | None:
        val = resp.headers.get("retry-after") or resp.headers.get("Retry-After")
        if not val:
            return None
        try:
            return float(val)
        except ValueError:
            # HTTP-date not handled for simplicity
            return None

    async def _robots_allowed(self, host: str, url: str) -> tuple[bool, float | None]:
        now = time.monotonic()
        rules = self._robots_cache.get(host)
        if rules is None or rules.expires_at <= now:
            rules = await self._fetch_robots(host)
            rules.expires_at = now + self.robots_ttl_s
            self._robots_cache[host] = rules
        if rules.backoff_until > now:
            return False, rules.crawl_delay
        path = urlparse(url).path or "/"
        return _robots_allows(rules, path), rules.crawl_delay

    async def _fetch_robots(self, host: str) -> _RobotsRules:
        # Try https then http.
        for scheme in ("https", "http"):
            try:
                resp_result = await self.transport.send(
                    HttpRequest(
                        method="GET",
                        url=f"{scheme}://{host}/robots.txt",
                        timeout_s=10.0,
                        max_bytes=1_000_000,
                        respect_robots=False,
                    )
                )
            except Exception:  # noqa: BLE001
                continue
            if isinstance(resp_result, Err):
                continue
            resp = resp_result.value
            if resp.status == 404:
                r = _RobotsRules(allowed=True)
                return r
            if resp.status >= 500:
                r = _RobotsRules(allowed=False)
                r.backoff_until = time.monotonic() + 6 * 3600
                return r
            if resp.status == 200:
                text = resp.body.decode("utf-8", "ignore")
                r = _parse_robots(text, self.user_agent)
                r.allowed = True
                return r
        # Unreachable ↁEtemp deny.
        r = _RobotsRules(allowed=False)
        r.backoff_until = time.monotonic() + 6 * 3600
        return r
