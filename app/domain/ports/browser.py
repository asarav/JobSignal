"""Browser ports (spec §13.1/§13.5).

``BrowserPage`` is an opaque handle: the capability-restricted session lives in
``FillPage`` (fill/select/check/upload/read) and in the provider, which exposes
no generic click, press, evaluate or dispatch. Step navigation exists only as
``click_step_control`` and the provider must route it through
``SubmissionGuard``.
"""

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

    async def close_session(self, session_id: str) -> None: ...

    async def close_all(self) -> None: ...


class FormInspector(Protocol):
    async def inspect(self, page: BrowserPage) -> Result[FormModel, PortError]: ...


class FormFiller(Protocol):
    async def plan(self, form: FormModel, answers: AnswerBook) -> FillPlan: ...

    async def apply(self, page: AutofillPage, plan: FillPlan) -> FillReport: ...


class AutofillPage(Protocol):
    """The restricted handle an ``apply`` call receives."""

    session_id: str

    async def goto(self, url: str) -> None: ...

    async def snapshot_dom(self) -> str: ...

    async def fill_text(self, selector: str, value: str) -> None: ...

    async def select_option(self, selector: str, value: str) -> None: ...

    async def choose_radio(self, selector: str, value: str) -> None: ...

    async def set_checked(self, selector: str, checked: bool) -> None: ...

    async def upload_file(self, selector: str, path: str) -> None: ...

    async def read_value(self, selector: str) -> str: ...

    async def read_validation(self, selector: str) -> str | None: ...

    async def screenshot(self, path: str) -> None: ...

    async def click_step_control(self, control_id: str) -> None:
        """Advance a step. Implementations must run the submission guard."""
        ...
