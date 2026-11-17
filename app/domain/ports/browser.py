"""Browser ports."""

from __future__ import annotations

from typing import Protocol

from app.domain.models.browser import (
    AnswerBook,
    BrowserPage,
    FillPlan,
    FillReport,
    FormModel,
    OpenOptions,
)
from app.domain.models.common import PortError, Result


class BrowserProvider(Protocol):
    id: str

    async def open(self, url: str, opts: OpenOptions) -> Result[BrowserPage, PortError]: ...

    async def rendered_html(self, session_id: str) -> str | None: ...

    async def robots_allowed(self, url: str) -> bool:
        """Whether ``url`` may be fetched, per robots.txt.

        The default is permissive: a provider that cannot answer the question
        must not block extraction silently.
        """
        return True

    async def close_all(self) -> None: ...


class FormInspector(Protocol):
    async def inspect(self, page: BrowserPage) -> Result[FormModel, PortError]: ...


class FormFiller(Protocol):
    async def plan(self, form: FormModel, answers: AnswerBook) -> FillPlan: ...

    async def apply(self, page: BrowserPage, plan: FillPlan) -> FillReport: ...
