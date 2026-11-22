"""browser.* handlers (M7).

Every response states ``submitted: false``; the worker has no method that could
change it (spec §13.5, allowlist-tested).
"""

from __future__ import annotations

from app.application.commands.browser import BrowserUseCase
from app.application.dto import (
    BrowserAutofillParams,
    BrowserCloseParams,
    BrowserHandoffParams,
    BrowserInspectParams,
    BrowserOpenJobParams,
    BrowserPlanParams,
)


class BrowserHandlers:
    def __init__(self, browser: BrowserUseCase) -> None:
        self._browser = browser

    async def open_job(self, params: BrowserOpenJobParams) -> dict:
        return (await self._browser.open_job(params)).model_dump()

    async def inspect_form(self, params: BrowserInspectParams) -> dict:
        return (await self._browser.inspect_form(params)).model_dump()

    async def autofill_plan(self, params: BrowserPlanParams) -> dict:
        return (await self._browser.plan(params)).model_dump()

    async def autofill(self, params: BrowserAutofillParams) -> dict:
        return (await self._browser.autofill(params)).model_dump()

    async def handoff(self, params: BrowserHandoffParams) -> dict:
        return (await self._browser.handoff(params)).model_dump()

    async def close_session(self, params: BrowserCloseParams) -> dict:
        return (await self._browser.close_session(params)).model_dump()
