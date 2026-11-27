"""Playwright-backed ``BrowserProvider`` (M2, optional).

Playwright is an *optional* dependency (``pip install lodestar[render]`` plus
``playwright install chromium``). When it is missing, :class:`PlaywrightBrowserProvider`
is still importable, but ``open`` returns a ``PortError`` so callers can fall
back to the static strategies.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from app.domain.models.browser import BrowserPage, OpenOptions
from app.domain.models.common import Err, Ok, PortError, Result


class _RobotsRules:
    """Minimal robots.txt parser: only ``User-agent: *`` Disallow rules."""

    def __init__(self, disallowed: list[str]) -> None:
        self._disallowed = disallowed

    @classmethod
    def allow_all(cls) -> _RobotsRules:
        return cls([])

    @classmethod
    def parse(cls, text: str) -> _RobotsRules:
        disallowed: list[str] = []
        active = False
        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line:
                continue
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key = key.strip().lower()
            value = value.strip()
            if key == "user-agent":
                active = value == "*"
            elif key == "disallow" and active and value:
                disallowed.append(value)
        return cls(disallowed)

    def is_allowed(self, path: str) -> bool:
        for rule in self._disallowed:
            if rule == "/":
                return False
            if path.startswith(rule):
                return False
        return True


class PlaywrightBrowserProvider:
    """A ``BrowserProvider`` backed by Playwright's Chromium."""

    id = "browser.playwright"

    def __init__(self, *, headless: bool = True, timeout_s: float = 30.0) -> None:
        self._headless = headless
        self._timeout_s = timeout_s
        self._playwright: Any = None
        self._browser: Any = None
        self._pages: dict[str, Any] = {}
        self._robots_cache: dict[str, _RobotsRules] = {}

    async def open(self, url: str, opts: OpenOptions) -> Result[BrowserPage, PortError]:
        try:
            await self._ensure_playwright()
        except Exception as exc:  # noqa: BLE001 - missing binary is expected
            return Err(
                PortError(
                    code="browser.playwright_unavailable",
                    message=f"Playwright is not available: {exc}",
                    retryable=False,
                )
            )
        try:
            page = await self._browser.new_page()
            await page.goto(url, timeout=int(opts.timeout_s * 1000), wait_until="domcontentloaded")
            session_id = str(id(page))
            self._pages[session_id] = page
            return Ok(BrowserPage(session_id=session_id, url=page.url))
        except Exception as exc:  # noqa: BLE001 - navigation can fail in many ways
            return Err(
                PortError(
                    code="browser.open_failed",
                    message=f"could not open {url}: {exc}",
                    retryable=True,
                )
            )

    async def rendered_html(self, session_id: str) -> str | None:
        page = self._pages.get(session_id)
        if page is None:
            return None
        return await page.content()

    async def robots_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc
        if host in self._robots_cache:
            return self._robots_cache[host].is_allowed(parsed.path or "/")
        allowed = _RobotsRules.allow_all()
        if self._browser is not None:
            try:
                temp_page = await self._browser.new_page()
                try:
                    robots_url = f"{parsed.scheme or 'https'}://{host}/robots.txt"
                    response = await temp_page.request.get(robots_url)
                    if response.status == 200:
                        body = await response.text()
                        if body:
                            allowed = _RobotsRules.parse(body)
                finally:
                    await temp_page.close()
            except Exception:  # noqa: BLE001
                allowed = _RobotsRules.allow_all()
        self._robots_cache[host] = allowed
        return allowed.is_allowed(parsed.path or "/")

    async def close_session(self, session_id: str) -> None:
        page = self._pages.pop(session_id, None)
        if page is None:
            return
        try:
            await page.close()
        except Exception:  # noqa: BLE001 - closing must never fail a request
            pass

    async def close_all(self) -> None:
        for page in list(self._pages.values()):
            try:
                await page.close()
            except Exception:  # noqa: BLE001
                pass
        self._pages.clear()
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self._playwright = None

    async def _ensure_playwright(self) -> Any:
        if self._playwright is not None:
            return self._playwright
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        return self._playwright
