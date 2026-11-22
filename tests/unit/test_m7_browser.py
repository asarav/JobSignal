"""Browser use case: inspect → plan → review → fill, never submit (§13.4)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from app.application.commands.browser import BrowserUseCase
from app.application.config import AppConfig
from app.application.dto import (
    BrowserAutofillParams,
    BrowserCloseParams,
    BrowserHandoffParams,
    BrowserInspectParams,
    BrowserOpenJobParams,
    BrowserPlanParams,
)
from app.domain.models.browser import BrowserPage
from app.domain.models.common import Ok, PortError
from app.domain.services.submission_guard import SubmissionGuardError
from app.infrastructure.browser import playwright_available
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from tests.support.containers import build_test_container
from tests.support.fakes import FakeClock

FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "application_site" / "generic.html"
)


class FakeHandle:
    """Records fills; there is no click or submit method to call."""

    def __init__(self) -> None:
        self.session_id = "session-1"
        self.values: dict[str, str] = {}
        self.checked: list[tuple[str, bool]] = []
        self.submissions = 0
        self.html = FIXTURE.read_text(encoding="utf-8")

    async def goto(self, url: str) -> None:
        return None

    async def snapshot_dom(self) -> str:
        return self.html

    async def fill_text(self, selector: str, value: str) -> None:
        self.values[selector] = value

    async def select_option(self, selector: str, value: str) -> None:
        self.values[selector] = value

    async def choose_radio(self, selector: str, value: str) -> None:
        self.values[selector] = value

    async def set_checked(self, selector: str, checked: bool) -> None:
        self.values[selector] = "yes" if checked else "no"
        self.checked.append((selector, checked))

    async def upload_file(self, selector: str, path: str) -> None:
        self.values[selector] = path

    async def read_value(self, selector: str) -> str:
        return self.values.get(selector, "")

    async def read_validation(self, selector: str) -> str | None:
        return None

    async def screenshot(self, path: str) -> None:
        return None

    async def click_step_control(self, control_id: str) -> None:
        raise SubmissionGuardError("step navigation is disabled")


class FakeProvider:
    id = "browser.fake"

    def __init__(self, handle: FakeHandle) -> None:
        self.handle_obj = handle
        self.opened: list[str] = []
        self.closed: list[str] = []
        self.robots = True
        self.handoffs: list[str] = []
        self.rearmed: list[str] = []
        self._handed_off: set[str] = set()

    def rearm(self, session_id: str) -> None:
        self.rearmed.append(session_id)
        self._handed_off.discard(session_id)

    def handoff(self, session_id: str) -> None:
        self.handoffs.append(session_id)
        self._handed_off.add(session_id)

    def interceptor_active(self, session_id: str) -> bool:
        return session_id not in self._handed_off

    async def robots_allowed(self, url: str) -> bool:
        return self.robots

    async def open(self, url: str, opts):
        self.opened.append(url)
        self.handle_obj.session_id = f"session-{len(self.opened)}"
        return Ok(BrowserPage(session_id=self.handle_obj.session_id, url=url))

    async def snapshot_dom(self, session_id: str) -> str:
        return self.handle_obj.html

    def handle(self, session_id: str) -> FakeHandle:
        return self.handle_obj

    async def close_session(self, session_id: str) -> None:
        self.closed.append(session_id)


def _container(
    provider: FakeProvider | None,
    *,
    autofill: bool = True,
    patch: dict | None = None,
):
    clock = FakeClock()
    factory = InMemoryUnitOfWorkFactory()
    factory.jobs["job_1"] = {
        "id": "job_1",
        "company_id": "co_1",
        "title": "Backend Engineer",
        "canonical_url": "https://example.test/apply",
        "posting_status": "active",
        "source_id": "src_1",
        "provenance": [],
    }
    config = AppConfig()
    config = config.model_copy(
        update={"features": config.features.model_copy(update={"autofill": autofill})}
    )
    if patch:
        config = config.apply_patch(patch)
    base = build_test_container(uow_factory=factory, clock=clock, config=config)
    extra = dict(base.extra)
    if provider is not None:
        extra["browser_provider"] = provider
    return replace(base, extra=extra), factory


@pytest.mark.asyncio
async def test_inspect_plan_and_fill_never_submits():
    handle = FakeHandle()
    provider = FakeProvider(handle)
    container, factory = _container(provider)

    async with factory() as uow:
        await uow.settings.put(
            "autofill_answers",
            {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com"},
        )
        await uow.commit()

    browser = BrowserUseCase(container)
    session = await browser.open_job(BrowserOpenJobParams(job_id="job_1"))
    assert session.session_id == "session-1"
    assert provider.opened == ["https://example.test/apply"]

    form = await browser.inspect_form(BrowserInspectParams(session_id=session.session_id))
    assert form.is_final_step is True
    assert any(field["classification"] == "blocked_legal" for field in form.fields)
    assert any(control["role"] == "submit" for control in form.controls)

    plan = await browser.plan(BrowserPlanParams(session_id=session.session_id))
    actions = {item["label"]: item["action"] for item in plan.items}
    assert actions["First name"] == "fill"
    assert actions["Email"] == "fill"
    assert actions["Gender"] == "block"
    assert actions["Social security number"] == "block"

    report = await browser.autofill(
        BrowserAutofillParams(session_id=session.session_id, plan_id=plan.plan_id)
    )
    assert report.submitted is False
    assert {item["label"] for item in report.filled} >= {"First name", "Email"}
    assert all(checked for _, checked in handle.checked)
    assert handle.submissions == 0
    assert not any(
        "gender" in selector or "certify" in selector for selector, _ in handle.checked
    )

    await browser.close_session(BrowserCloseParams(session_id=session.session_id))
    assert provider.closed == ["session-1"]
    with pytest.raises(PortError):
        await browser.autofill(
            BrowserAutofillParams(session_id="session-1", plan_id=plan.plan_id)
        )


@pytest.mark.asyncio
async def test_missing_provider_reports_unavailable():
    container, _ = _container(None)
    browser = BrowserUseCase(container)
    with pytest.raises(PortError) as excinfo:
        await browser.open_job(BrowserOpenJobParams(job_id="job_1"))
    assert excinfo.value.code == "browser.unavailable"


@pytest.mark.asyncio
async def test_feature_flag_disables_autofill():
    provider = FakeProvider(FakeHandle())
    container, _ = _container(provider, autofill=False)
    browser = BrowserUseCase(container)
    with pytest.raises(PortError) as excinfo:
        await browser.open_job(BrowserOpenJobParams(job_id="job_1"))
    assert excinfo.value.code == "feature_disabled"
    assert provider.opened == []


@pytest.mark.asyncio
async def test_robots_disallowed_url_is_not_opened():
    provider = FakeProvider(FakeHandle())
    provider.robots = False
    container, _ = _container(provider)
    browser = BrowserUseCase(container)
    with pytest.raises(PortError) as excinfo:
        await browser.open_job(BrowserOpenJobParams(job_id="job_1"))
    assert excinfo.value.code == "crawl.robots_disallowed"
    assert provider.opened == []


@pytest.mark.asyncio
async def test_plan_for_unknown_session_is_rejected():
    container, _ = _container(FakeProvider(FakeHandle()))
    browser = BrowserUseCase(container)
    plan = await browser.plan(BrowserPlanParams(session_id="ghost"))
    with pytest.raises(PortError):
        await browser.autofill(
            BrowserAutofillParams(session_id="other", plan_id=plan.plan_id)
        )


def test_provider_reports_playwright_availability():
    # The optional dependency may or may not be installed; either way the module
    # must import and answer the question instead of crashing.
    assert isinstance(playwright_available(), bool)


@pytest.mark.asyncio
async def test_plan_uses_the_current_dom_not_a_cached_model():
    handle = FakeHandle()
    container, factory = _container(FakeProvider(handle))
    async with factory() as uow:
        await uow.settings.put(
            "autofill_answers", {"first_name": "Ada", "last_name": "Lovelace"}
        )
        await uow.commit()

    browser = BrowserUseCase(container)
    session = await browser.open_job(BrowserOpenJobParams(job_id="job_1"))
    await browser.inspect_form(BrowserInspectParams(session_id=session.session_id))

    # The user advanced a step; the cached model is now wrong.
    handle.html = (
        '<form><label for="last_name">Last name</label>'
        '<input id="last_name" name="last_name"></form>'
    )
    plan = await browser.plan(BrowserPlanParams(session_id=session.session_id))
    labels = {item["label"] for item in plan.items}
    assert labels == {"Last name"}
    assert plan.items[0]["action"] == "fill"


@pytest.mark.asyncio
async def test_reopening_the_same_job_closes_the_previous_window():
    provider = FakeProvider(FakeHandle())
    container, _ = _container(provider)
    browser = BrowserUseCase(container)

    first = await browser.open_job(BrowserOpenJobParams(job_id="job_1"))
    second = await browser.open_job(BrowserOpenJobParams(job_id="job_1"))

    assert provider.closed == [first.session_id]
    assert second.session_id != first.session_id


@pytest.mark.asyncio
async def test_answer_book_can_come_from_typed_settings():
    container, _ = _container(
        FakeProvider(FakeHandle()),
        patch={"autofill": {"answers": {"first_name": "Grace"}}},
    )
    browser = BrowserUseCase(container)
    session = await browser.open_job(BrowserOpenJobParams(job_id="job_1"))
    plan = await browser.plan(BrowserPlanParams(session_id=session.session_id))
    first_name = next(item for item in plan.items if item["label"] == "First name")
    assert first_name["action"] == "fill"
    assert first_name["value"] == "Grace"


@pytest.mark.asyncio
async def test_saved_answer_book_applies_without_a_restart():
    from app.application.config import ConfigState

    config = AppConfig()
    state = ConfigState(config)
    container, _ = _container(FakeProvider(FakeHandle()))
    container = replace(container, extra={**container.extra, "config_state": state})
    browser = BrowserUseCase(container)
    session = await browser.open_job(BrowserOpenJobParams(job_id="job_1"))

    before = await browser.plan(BrowserPlanParams(session_id=session.session_id))
    assert next(item for item in before.items if item["label"] == "First name")[
        "action"
    ] == "skip"

    state.set(config.apply_patch({"autofill": {"answers": {"first_name": "Grace"}}}))
    after = await browser.plan(BrowserPlanParams(session_id=session.session_id))
    item = next(item for item in after.items if item["label"] == "First name")
    assert item["action"] == "fill"
    assert item["value"] == "Grace"


@pytest.mark.asyncio
async def test_handoff_is_explicit_and_the_next_fill_re_arms_it():
    handle = FakeHandle()
    provider = FakeProvider(handle)
    container, factory = _container(provider)
    async with factory() as uow:
        await uow.settings.put("autofill_answers", {"first_name": "Ada"})
        await uow.commit()

    browser = BrowserUseCase(container)
    session = await browser.open_job(BrowserOpenJobParams(job_id="job_1"))
    plan = await browser.plan(BrowserPlanParams(session_id=session.session_id))

    # Only the user, through this method, can stop the interceptor.
    result = await browser.handoff(BrowserHandoffParams(session_id=session.session_id))
    assert result.submitted is False
    assert result.interceptor_active is False
    assert provider.handoffs == [session.session_id]

    # Filling writes values, so the guard comes back on before the first write.
    report = await browser.autofill(
        BrowserAutofillParams(session_id=session.session_id, plan_id=plan.plan_id)
    )
    assert provider.rearmed == [session.session_id]
    assert provider.interceptor_active(session.session_id) is True
    assert report.submitted is False
    assert handle.submissions == 0


@pytest.mark.asyncio
async def test_max_fills_bounds_one_step():
    container, factory = _container(
        FakeProvider(FakeHandle()),
        patch={"autofill": {"max_fills": 1}},
    )
    async with factory() as uow:
        await uow.settings.put(
            "autofill_answers", {"first_name": "Ada", "last_name": "Lovelace"}
        )
        await uow.commit()
    browser = BrowserUseCase(container)
    session = await browser.open_job(BrowserOpenJobParams(job_id="job_1"))
    plan = await browser.plan(BrowserPlanParams(session_id=session.session_id))
    fills = [item for item in plan.items if item["action"] == "fill"]
    assert len(fills) == 1
    assert any("per-step autofill limit" in item["reason"] for item in plan.items)
