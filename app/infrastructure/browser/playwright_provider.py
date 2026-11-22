"""Playwright Chromium provider (spec §13.1/§13.5).

The page handle exposes only fill/select/check/upload/read/screenshot. There is
no click, press or evaluate anywhere in this module: ``click_step_control``
exists to satisfy the port and always refuses, because step navigation is
disabled in this build and advancing a step must stay a user action.

Submission requests are aborted for the **whole life of a session**, not just
while the filler is writing (§13.5.4). A page handler that submits on ``input``
may schedule its POST seconds later; a time-boxed window would let that request
through, so the guard is armed when the page opens and every fill re-arms it.
The one way to turn it off is ``handoff``: the user explicitly saying "I am
submitting this myself now" (ADR 0008). That keeps §13.4.5's manual submission
possible without giving the automation any window in which a submission can
slip through.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from app.domain.models.browser import BrowserPage, OpenOptions
from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.services.submission_guard import SubmissionGuard, SubmissionGuardError

try:  # Playwright is an optional dependency.
    from playwright.async_api import (  # type: ignore[import-not-found]
        async_playwright,
    )
except Exception:  # noqa: BLE001 - a missing browser is a normal, reported state
    async_playwright = None  # type: ignore[assignment]


#: Refuse to hand a gigantic DOM to the parser; a page this big is hostile or
#: broken, and the parser would only waste memory on it.
MAX_DOM_CHARS = 4_000_000

AUTH_WALL_SELECTOR = "input[type=password]"
CHALLENGE_SELECTORS = (
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "[data-sitekey]",
)


def playwright_available() -> bool:
    return async_playwright is not None


def _css_string(value: str) -> str:
    """Escape page-supplied text used inside a quoted CSS attribute value."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return escaped.replace("\n", "\\a ").replace("\r", "\\d ")


def _with_value(selector: str, value: str) -> str:
    return f'{selector}[value="{_css_string(value)}"]'


class PlaywrightPageHandle:
    """The capability-restricted handle handed to ``FormFiller.apply``."""

    def __init__(self, provider: PlaywrightBrowserProvider, session_id: str) -> None:
        self.session_id = session_id
        self._provider = provider

    async def goto(self, url: str) -> None:
        await self._provider.goto(self.session_id, url)

    async def snapshot_dom(self) -> str:
        return await self._provider.snapshot_dom(self.session_id)

    async def fill_text(self, selector: str, value: str) -> None:
        page = self._provider.page(self.session_id)
        await page.locator(selector).first.fill(value)

    async def select_option(self, selector: str, value: str) -> None:
        page = self._provider.page(self.session_id)
        locator = page.locator(selector).first
        try:
            await locator.select_option(value=value)
        except Exception:
            # Some controls accept only a label; fall back to it explicitly.
            await locator.select_option(label=value)

    async def choose_radio(self, selector: str, value: str) -> None:
        page = self._provider.page(self.session_id)
        await page.locator(_with_value(selector, value)).first.check()

    async def set_checked(self, selector: str, checked: bool) -> None:
        page = self._provider.page(self.session_id)
        locator = page.locator(selector).first
        if checked:
            await locator.check()
        else:
            await locator.uncheck()

    async def upload_file(self, selector: str, path: str) -> None:
        page = self._provider.page(self.session_id)
        await page.locator(selector).first.set_input_files(path)

    async def read_value(self, selector: str) -> str:
        page = self._provider.page(self.session_id)
        locator = page.locator(selector)
        try:
            kind = await locator.first.get_attribute("type")
        except Exception:  # noqa: BLE001 - a non-input has no type to read
            kind = None
        if (kind or "").lower() == "radio":
            # A radio group has no single value: what it holds is whichever
            # option is checked. ``.first`` would read the first option's value
            # and report a correct answer as one the page did not accept.
            checked = page.locator(f"{selector}:checked")
            if await checked.count() > 0:
                return await checked.first.input_value()
            return ""
        try:
            return await locator.first.input_value()
        except Exception:
            return await locator.first.inner_text()

    async def read_validation(self, selector: str) -> str | None:
        page = self._provider.page(self.session_id)
        locator = page.locator(selector).first
        try:
            message_id = await locator.get_attribute("aria-errormessage")
            if message_id:
                text = await page.locator(
                    f'[id="{_css_string(message_id)}"]'
                ).first.inner_text()
                if text.strip():
                    return text.strip()
            invalid = await locator.get_attribute("aria-invalid")
        except Exception:
            return None
        return "invalid" if invalid == "true" else None

    async def screenshot(self, path: str) -> None:
        page = self._provider.page(self.session_id)
        await page.screenshot(path=path)

    async def click_step_control(self, control_id: str) -> None:
        # Step navigation is off in this build and this provider has no click
        # capability at all; refusing is the only correct behaviour.
        raise SubmissionGuardError(
            "step navigation is disabled; advance the form yourself"
        )


class PlaywrightBrowserProvider:
    id = "browser.playwright"
    version = "1.0.0"

    def __init__(
        self,
        guard: SubmissionGuard | None = None,
        *,
        max_sessions: int = 4,
    ) -> None:
        self._guard = guard or SubmissionGuard()
        self._playwright: Any = None
        self._browser: Any = None
        self._sessions: dict[str, tuple[Any, Any]] = {}
        #: Sessions whose guard the user explicitly turned off by handing the
        #: window over to submit themselves. Every fill re-arms.
        self._handed_off: set[str] = set()
        self._headless: bool | None = None
        self._blocked_requests = 0
        self._max_sessions = max(1, max_sessions)

    @property
    def available(self) -> bool:
        return playwright_available()

    @property
    def blocked_requests(self) -> int:
        """How many submission requests the guard aborted (diagnostics/tests)."""
        return self._blocked_requests

    def page(self, session_id: str) -> Any:
        session = self._sessions.get(session_id)
        if session is None:
            raise PortError(
                code="not_found",
                message=f"unknown browser session: {session_id}",
                retryable=False,
            )
        return session[1]

    def handle(self, session_id: str) -> PlaywrightPageHandle:
        return PlaywrightPageHandle(self, session_id)

    # -- submission interceptor --------------------------------------------

    def interceptor_active(self, session_id: str) -> bool:
        """Whether submission-shaped requests are still being aborted."""
        return session_id in self._sessions and session_id not in self._handed_off

    def handoff(self, session_id: str) -> None:
        """Turn the interceptor off because the user is submitting themselves.

        This is the single, explicit, user-initiated way out (§13.4.5). Nothing
        in the automation calls it, and ``rearm`` undoes it the moment the user
        asks for another fill.
        """
        if session_id not in self._sessions:
            raise PortError(
                code="not_found",
                message=f"unknown browser session: {session_id}",
                retryable=False,
            )
        self._handed_off.add(session_id)

    def rearm(self, session_id: str) -> None:
        """A fill wrote values, so the page must not be able to submit them."""
        self._handed_off.discard(session_id)

    # -- browser lifecycle -------------------------------------------------

    async def _ensure_browser(self, *, headed: bool) -> Any:
        wanted_headless = not headed
        if self._browser is not None and self._headless == wanted_headless:
            return self._browser
        if self._browser is not None:
            await self._close_browser()
        if async_playwright is None:
            raise PortError(
                code="browser.unavailable",
                message=(
                    "Playwright is not installed. Install the optional browser "
                    "extra to use autofill; everything else keeps working."
                ),
                retryable=False,
            )
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=wanted_headless)
        self._headless = wanted_headless
        self._blocked_requests = 0
        return self._browser

    async def _close_browser(self) -> None:
        # Every context died with the browser; drop handles to them so a later
        # call reports ``not_found`` instead of a stale page.
        self._sessions.clear()
        self._handed_off.clear()
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001 - closing must never fail a request
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self._playwright = None
        self._headless = None

    async def _evict_if_needed(self) -> None:
        while len(self._sessions) >= self._max_sessions:
            oldest = next(iter(self._sessions))
            await self.close_session(oldest)

    async def open(
        self, url: str, opts: OpenOptions
    ) -> Result[BrowserPage, PortError]:
        session_id = uuid.uuid4().hex
        context: Any = None
        try:
            browser = await self._ensure_browser(headed=opts.headed)
            await self._evict_if_needed()
            context = await browser.new_context()

            async def handler(route: Any, request: Any) -> None:
                await self._route(route, request, session_id)

            await context.route("**/*", handler)
            page = await context.new_page()
            await page.goto(url, timeout=opts.timeout_s * 1000)

            wall = await self._wall(page)
            if wall is not None:
                await context.close()
                return Err(wall)
        except PortError as exc:
            if context is not None:
                await _safe_close(context)
            return Err(exc)
        except Exception as exc:  # noqa: BLE001 - reported, never fatal
            if context is not None:
                await _safe_close(context)
            return Err(
                PortError(
                    code="browser.open_failed",
                    message=str(exc),
                    retryable=True,
                )
            )
        self._sessions[session_id] = (context, page)
        return Ok(BrowserPage(session_id=session_id, url=page.url or url))

    async def _wall(self, page: Any) -> PortError | None:
        """Stop at a *visible* login or CAPTCHA wall instead of filling around it."""
        try:
            passwords = page.locator(AUTH_WALL_SELECTOR)
            for index in range(min(await passwords.count(), 5)):
                if await passwords.nth(index).is_visible():
                    return PortError(
                        code="auth_required",
                        message=(
                            "the page asks for a password; sign in yourself, then "
                            "re-check the form"
                        ),
                        retryable=False,
                    )
            for selector in CHALLENGE_SELECTORS:
                challenge = page.locator(selector)
                if await challenge.count() > 0 and await challenge.first.is_visible():
                    return PortError(
                        code="browser.challenge",
                        message=(
                            "the page shows a CAPTCHA or bot check; complete it "
                            "yourself, then re-check the form"
                        ),
                        retryable=False,
                    )
        except Exception:  # noqa: BLE001 - a failed probe must not block the open
            return None
        return None

    async def _route(self, route: Any, request: Any, session_id: str) -> None:
        """Abort submission-shaped requests unless the user has taken over."""
        if not self.interceptor_active(session_id):
            await route.continue_()
            return
        body = ""
        try:
            body = request.post_data or ""
        except Exception:  # noqa: BLE001 - binary or unavailable bodies
            body = ""
        if self._guard.is_submission_request(
            method=request.method, url=request.url, body=body
        ):
            self._blocked_requests += 1
            await route.abort()
            return
        await route.continue_()

    async def goto(self, session_id: str, url: str) -> None:
        await self.page(session_id).goto(url)

    async def snapshot_dom(self, session_id: str) -> str:
        html = await self.page(session_id).content()
        if len(html) > MAX_DOM_CHARS:
            raise PortError(
                code="browser.page_too_large",
                message=f"page markup exceeds {MAX_DOM_CHARS} characters",
                retryable=False,
            )
        return html

    async def rendered_html(self, session_id: str) -> str | None:
        try:
            return await self.snapshot_dom(session_id)
        except PortError:
            return None

    async def robots_allowed(self, url: str) -> bool:
        return True

    async def close_session(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        self._handed_off.discard(session_id)
        if session is None:
            return
        context, _ = session
        await _safe_close(context)

    async def close_all(self) -> None:
        for session_id in list(self._sessions):
            await self.close_session(session_id)
        self._handed_off.clear()
        await self._close_browser()


async def _safe_close(context: Any) -> None:
    try:
        await context.close()
    except Exception:  # noqa: BLE001 - closing must never fail a request
        pass


#: Mapping helpers used by tests to assert the handle's surface.
RESTRICTED_CAPABILITIES: tuple[str, ...] = (
    "goto",
    "snapshot_dom",
    "fill_text",
    "select_option",
    "set_checked",
    "choose_radio",
    "upload_file",
    "read_value",
    "read_validation",
    "screenshot",
    "click_step_control",
)


def capability_surface() -> Mapping[str, tuple[str, ...]]:
    return {"browser.playwright": RESTRICTED_CAPABILITIES}
