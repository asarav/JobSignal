"""Playwright-backed ``BrowserProvider`` (M2, optional).

Playwright is an *optional* dependency (``pip install lodestar[render]`` plus
``playwright install chromium``). When it is missing, :class:`PlaywrightBrowserProvider`
is still importable, but ``open`` returns a ``PortError`` so callers can fall
back to the static strategies.
"""

from __future__ import annotations

from typing import Any

from app.domain.models.browser import BrowserPage, OpenOptions
from app.domain.models.common import Err, Ok, PortError, Result


class PlaywrightBrowserProvider:
    """A ``BrowserProvider`` backed by Playwright's Chromium."""

    id = "browser.playwright"

    def __init__(self, *, headless: bool = True, timeout_s: float = 30.0) -> None:
        self._headless = headless
        self._timeout_s = timeout_s
        self._playwright: Any = None
        self._browser: Any = None
        self._pages: dict[str, Any] = {}

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
            await page.goto(url, timeout=int(opts.timeout_s * 1000), wait_until="networkidle")
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

    async def rendered_html(self, session_id: str) -> str | None:
        """Return the fully rendered DOM of an open page."""
        page = self._pages.get(session_id)
        if page is None:
            return None
        return await page.content()

    async def _ensure_playwright(self) -> Any:
        if self._playwright is not None:
            return self._playwright
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._headless)
        return self._playwright
