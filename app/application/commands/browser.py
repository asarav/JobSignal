"""Browser session, inspection and autofill use cases (spec §13.4).

The provider is injected by the composition root (``container.extra``), so this
module never imports Playwright. Every path that could submit is guarded here
by construction: the only mutating call is ``FormFiller.apply``, which uses the
restricted page primitives.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.application.config import live_config
from app.application.container import Container
from app.application.dto import (
    BrowserAutofillParams,
    BrowserCloseParams,
    BrowserFillReportResult,
    BrowserFormResult,
    BrowserHandoffParams,
    BrowserHandoffResult,
    BrowserInspectParams,
    BrowserOpenJobParams,
    BrowserPlanParams,
    BrowserPlanResult,
    BrowserSessionResult,
    OkResult,
)
from app.domain.models.browser import (
    AnswerBook,
    FillPlan,
    FormModel,
    OpenOptions,
)
from app.domain.models.common import Err, PortError
from app.domain.services.form_filler import GenericFormFiller
from app.domain.services.form_inspector import inspect_html
from app.domain.services.submission_guard import SubmissionGuard

#: Above this many open sessions the oldest is closed, so a user clicking
#: "check form" repeatedly cannot accumulate browser windows forever.
MAX_OPEN_SESSIONS = 4

ANSWER_SETTING_KEY = "autofill_answers"
SNIPPET_SETTING_KEY = "autofill_snippets"


class BrowserUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container
        self._plans: dict[str, FillPlan] = {}
        self._urls: dict[str, str] = {}
        self._job_sessions: dict[str, str] = {}
        self._session_order: list[str] = []
        #: Serializes work per job/session: the IPC layer runs up to eight
        #: requests at once, and two fills on one page must not interleave.
        self._locks: dict[str, asyncio.Lock] = {}

    # -- wiring -----------------------------------------------------------

    def _provider(self) -> Any:
        provider = self._container.extra.get("browser_provider")
        if provider is None:
            raise PortError(
                code="browser.unavailable",
                message="no browser provider is configured for this build",
                retryable=False,
            )
        return provider

    def _config(self) -> Any:
        return live_config(self._container)

    def _settings(self) -> Any:
        return self._config().autofill

    def _guard(self) -> SubmissionGuard:
        settings = self._settings()
        return SubmissionGuard(
            lexicon=tuple(settings.submit_lexicon),
            allow_step_navigation=bool(settings.allow_step_navigation),
        )

    def _filler(self) -> GenericFormFiller:
        return GenericFormFiller(
            guard=self._guard(),
            min_confidence=float(self._settings().min_field_confidence),
            max_fills=int(self._settings().max_fills),
            delay_s=float(self._settings().fill_delay_ms) / 1000.0,
        )

    def _require_feature(self) -> None:
        if not getattr(self._config().features, "autofill", False):
            raise PortError(
                code="feature_disabled",
                message="autofill is disabled in settings",
                retryable=False,
            )

    # -- commands ---------------------------------------------------------

    async def open_job(self, params: BrowserOpenJobParams) -> BrowserSessionResult:
        self._require_feature()
        key = f"job:{params.job_id}"
        lock = self._lock(key)
        async with lock:
            result = await self._open_job(params)
        # Keep the lock table bounded: one entry per job that is mid-open, not
        # one per job ever opened.
        if not lock.locked() and self._locks.get(key) is lock:
            del self._locks[key]
        return result

    async def _open_job(self, params: BrowserOpenJobParams) -> BrowserSessionResult:
        provider = self._provider()
        async with self._container.uow_factory() as uow:
            job = await uow.jobs.get(params.job_id)
            await uow.commit()
        if job is None:
            raise PortError(
                code="not_found",
                message=f"unknown job: {params.job_id}",
                retryable=False,
            )
        url = str(job.get("canonical_url") or "")
        if not url:
            raise PortError(
                code="validation.invalid_params",
                message="this job has no application URL",
                retryable=False,
            )
        if not await provider.robots_allowed(url):
            raise PortError(
                code="crawl.robots_disallowed",
                message=f"robots.txt disallows {url}",
                retryable=False,
            )
        # Re-opening the same job is a re-check, not a second window.
        previous = self._job_sessions.get(params.job_id)
        if previous:
            await self._discard(provider, previous)
        await self._evict_oldest(provider)
        outcome = await provider.open(url, OpenOptions(headed=True))
        if isinstance(outcome, Err):
            raise outcome.error
        session_id = str(outcome.value.session_id)
        self._urls[session_id] = url
        self._job_sessions[params.job_id] = session_id
        self._session_order.append(session_id)
        return BrowserSessionResult(
            session_id=session_id, url=url, job_id=params.job_id
        )

    async def inspect_form(self, params: BrowserInspectParams) -> BrowserFormResult:
        self._require_feature()
        provider = self._provider()
        model = await self._inspect(provider, params.session_id)
        return BrowserFormResult(
            session_id=model.session_id,
            url=model.url,
            step_index=model.step_index,
            step_count=model.step_count,
            is_final_step=model.is_final_step,
            fields=[
                {
                    "field_id": form_field.field_id,
                    "kind": form_field.kind,
                    "label": form_field.label or form_field.accessible_name,
                    "required": form_field.required,
                    "section": form_field.section_hint,
                    "hidden": form_field.hidden,
                    "classification": form_field.classification.field_class,
                    "confidence": form_field.classification.confidence,
                    "key": form_field.classification.key,
                }
                for form_field in model.fields
            ],
            controls=[
                {
                    "control_id": control.control_id,
                    "text": control.text,
                    "role": control.role,
                    "type": control.type_attr,
                }
                for control in model.controls
            ],
        )

    async def plan(self, params: BrowserPlanParams) -> BrowserPlanResult:
        self._require_feature()
        async with self._lock(f"session:{params.session_id}"):
            return await self._plan(params)

    async def _plan(self, params: BrowserPlanParams) -> BrowserPlanResult:
        provider = self._provider()
        # Always re-inspect: the user may have advanced a step or the page may
        # have changed since the last look (§13.4.2). A cached model would plan
        # against the wrong DOM and write into whatever now sits at that selector.
        model = await self._inspect(provider, params.session_id)
        answers = await self._answer_book()
        plan = self._filler().plan(model, answers)
        for stale_id, stale in list(self._plans.items()):
            if stale.session_id == plan.session_id:
                del self._plans[stale_id]
        self._plans[plan.plan_id] = plan
        return BrowserPlanResult(
            plan_id=plan.plan_id,
            session_id=plan.session_id,
            url=plan.url,
            step_index=plan.step_index,
            items=[
                {
                    "field_id": item.field_id,
                    "label": item.field_label,
                    "kind": item.kind,
                    "value": item.value,
                    "action": item.action,
                    "reason": item.reason,
                    "confidence": item.confidence,
                }
                for item in plan.items
            ],
        )

    async def autofill(self, params: BrowserAutofillParams) -> BrowserFillReportResult:
        self._require_feature()
        async with self._lock(f"session:{params.session_id}"):
            return await self._autofill(params)

    async def _autofill(
        self, params: BrowserAutofillParams
    ) -> BrowserFillReportResult:
        provider = self._provider()
        plan = self._plans.get(params.plan_id)
        if plan is None or plan.session_id != params.session_id:
            raise PortError(
                code="not_found",
                message="unknown autofill plan for this session",
                retryable=False,
            )
        handle = provider.handle(params.session_id)
        # Values are about to be written, so the page must not be able to submit
        # them — including from a handler that fires seconds later (§13.5.4).
        # A fill therefore re-arms the interceptor the user may have handed off.
        rearm = getattr(provider, "rearm", None)
        if rearm is not None:
            rearm(params.session_id)
        report = await self._filler().apply(handle, plan)
        return BrowserFillReportResult(
            plan_id=report.plan_id,
            filled=[_item(item) for item in report.filled],
            skipped=[_item(item) for item in report.skipped],
            blocked=[_item(item) for item in report.blocked],
            errors=list(report.errors),
            submitted=False,
        )

    async def handoff(self, params: BrowserHandoffParams) -> BrowserHandoffResult:
        """Stop intercepting submissions because the user is submitting now.

        Every other path keeps the interceptor armed; this is the explicit,
        user-initiated exit required by §13.4.5, and it submits nothing itself.
        """
        self._require_feature()
        provider = self._provider()
        handoff = getattr(provider, "handoff", None)
        if handoff is not None:
            handoff(params.session_id)
        active = getattr(provider, "interceptor_active", None)
        return BrowserHandoffResult(
            session_id=params.session_id,
            interceptor_active=bool(active(params.session_id)) if active else False,
        )

    async def close_session(self, params: BrowserCloseParams) -> OkResult:
        provider = self._container.extra.get("browser_provider")
        if provider is not None:
            await self._discard(provider, params.session_id)
        else:
            self._forget(params.session_id)
        return OkResult()

    # -- helpers ----------------------------------------------------------

    def _lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def _discard(self, provider: Any, session_id: str) -> None:
        try:
            await provider.close_session(session_id)
        except Exception:  # noqa: BLE001 - closing must never fail a request
            pass
        self._forget(session_id)

    def _forget(self, session_id: str) -> None:
        self._urls.pop(session_id, None)
        if session_id in self._session_order:
            self._session_order.remove(session_id)
        for job_id, open_session in list(self._job_sessions.items()):
            if open_session == session_id:
                del self._job_sessions[job_id]
                job_lock = self._locks.get(f"job:{job_id}")
                if job_lock is not None and not job_lock.locked():
                    del self._locks[f"job:{job_id}"]
        for plan_id, plan in list(self._plans.items()):
            if plan.session_id == session_id:
                del self._plans[plan_id]
        session_lock = self._locks.get(f"session:{session_id}")
        if session_lock is not None and not session_lock.locked():
            del self._locks[f"session:{session_id}"]

    async def _evict_oldest(self, provider: Any) -> None:
        while len(self._session_order) >= MAX_OPEN_SESSIONS:
            await self._discard(provider, self._session_order[0])

    async def _inspect(self, provider: Any, session_id: str) -> FormModel:
        html = await provider.snapshot_dom(session_id)
        return inspect_html(
            html,
            session_id=session_id,
            url=self._urls.get(session_id, ""),
            lexicon=tuple(self._settings().submit_lexicon),
        )

    async def _answer_book(self) -> AnswerBook:
        settings = self._settings()
        async with self._container.uow_factory() as uow:
            stored = await uow.settings.get(ANSWER_SETTING_KEY) or {}
            snippets = await uow.settings.get(SNIPPET_SETTING_KEY) or {}
            await uow.commit()
        # The typed config is the supported answer book; the standalone settings
        # rows are read too so answers stored before this section existed keep
        # working. Config wins on a conflict.
        answers = {
            **{
                str(key): str(value)
                for key, value in stored.items()
                if isinstance(value, str) and value.strip()
            },
            **{
                str(key): str(value)
                for key, value in dict(settings.answers).items()
                if isinstance(value, str) and value.strip()
            },
        }
        snippets_out = {
            **{
                str(key): str(value)
                for key, value in snippets.items()
                if isinstance(value, str) and value.strip()
            },
            **{
                str(key): str(value)
                for key, value in dict(settings.snippets).items()
                if isinstance(value, str) and value.strip()
            },
        }
        return AnswerBook(answers=answers, snippets=snippets_out)


def _item(item: Any) -> dict[str, Any]:
    return {
        "field_id": item.field_id,
        "label": item.field_label,
        "kind": item.kind,
        "value": item.value,
        "reason": item.reason,
        "confidence": item.confidence,
    }
