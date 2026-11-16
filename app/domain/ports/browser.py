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

    async def close_all(self) -> None: ...


class FormInspector(Protocol):
    async def inspect(self, page: BrowserPage) -> Result[FormModel, PortError]: ...


class FormFiller(Protocol):
    async def plan(self, form: FormModel, answers: AnswerBook) -> FillPlan: ...

    async def apply(self, page: BrowserPage, plan: FillPlan) -> FillReport: ...
