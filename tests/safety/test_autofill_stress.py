"""Stress and adversarial robustness for autofill (spec §13.5, §19.5).

Normal fixtures exercise ordinary forms. These tests exercise the shapes a
hostile or merely unusual page can take — very large forms, thousands of
options, deep nesting, quote-laden ids — and the guard's own surface, so a
regression shows up as a slow or unsafe build rather than a surprise in use.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from app.application.config import AppConfig
from app.domain.models.application import (
    AppliedJob,
    SuppressionCandidate,
    SuppressionPolicy,
)
from app.domain.models.browser import AnswerBook, FormControl, FormModel
from app.domain.services.form_filler import GenericFormFiller
from app.domain.services.form_inspector import inspect_html
from app.domain.services.submission_guard import SubmissionGuard, SubmissionGuardError
from app.domain.services.suppression import evaluate_suppression
from pydantic import ValidationError

ANSWERS = AnswerBook(answers={"first_name": "Ada", "email": "ada@example.com"})
NOW = datetime(2026, 6, 1, tzinfo=UTC)

BACKEND = (
    "We are hiring a backend engineer to build distributed services in Python and Go. "
    "You will own reliability, APIs and data models for a growing platform team."
)
ACCOUNTING = (
    "Own the monthly close, reconcile ledgers and prepare statutory filings for the "
    "group. Qualified accountant with IFRS experience required."
)


def _page(body: str) -> str:
    return f"<!doctype html><html><body>{body}</body></html>"


def _inspect(body: str) -> FormModel:
    return inspect_html(_page(body), session_id="session-1", url="https://example.test/form")


def test_deeply_nested_markup_does_not_hit_the_recursion_limit():
    depth = 2000
    body = (
        "<div>" * depth
        + '<label for="first_name">First name</label><input id="first_name" name="first_name">'
        + "</div>" * depth
    )
    started = time.perf_counter()
    model = _inspect(body)
    assert time.perf_counter() - started < 10
    assert any(field.classification.key == "first_name" for field in model.fields)


def test_large_form_is_inspected_and_planned_in_bounded_time():
    body = "".join(
        f'<label for="f{i}">First name</label><input id="f{i}" name="f{i}">'
        for i in range(3000)
    )
    started = time.perf_counter()
    model = _inspect(body)
    plan = GenericFormFiller(max_fills=100).plan(model, ANSWERS)
    elapsed = time.perf_counter() - started

    assert len(model.fields) == 3000
    assert len(plan.fills) == 100
    assert all("per-step autofill limit" in item.reason for item in plan.skipped)
    assert elapsed < 10


def test_radio_group_with_many_options_stays_linear_enough():
    options = "".join(
        f'<input type="radio" name="q" value="v{i}"><label>Option {i}</label>'
        for i in range(600)
    )
    started = time.perf_counter()
    model = _inspect(options)
    elapsed = time.perf_counter() - started

    assert len(model.fields) == 1
    assert len(model.fields[0].options) == 600
    assert elapsed < 5


def test_page_supplied_attribute_values_are_escaped_in_selectors():
    body = (
        "<label for='a\"b'>First name</label>"
        "<input id='a\"b' name='x\"]y'>"
        '<input type="text" name="line\nbreak" aria-label="Notes">'
    )
    model = _inspect(body)
    first = next(field for field in model.fields if field.label == "First name")
    notes = next(field for field in model.fields if field.accessible_name == "Notes")

    assert first.selector == '[id="a\\"b"]'
    assert "\\a " in notes.selector
    # A quoted attribute selector closes exactly once, at its end, so the
    # page cannot close the quote early and point at another element.
    for field in model.fields:
        if field.selector.startswith("["):
            assert field.selector.endswith('"]')
            assert field.selector.count('"]') == 1


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("POST", "https://x.test/apply", ""),
        ("POST", "https://x.test/applications", "a=1&b=2"),
        ("POST", "https://x.test/applications/complete", ""),
        ("PATCH", "https://x.test/jobs/123/apply", ""),
        ("PUT", "https://x.test/candidate/apply", ""),
        ("POST", "https://x.test/step", "action=submit&first_name=Ada"),
        ("POST", "https://x.test/step", "confirm_application=true"),
        ("POST", "https://x.test/step", "_action=submit"),
    ],
)
def test_submission_shaped_requests_are_blocked(method, url, body):
    assert SubmissionGuard().is_submission_request(method=method, url=url, body=body)


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("GET", "https://x.test/apply", ""),
        ("HEAD", "https://x.test/applications", ""),
        ("POST", "https://storage.test/upload", '{"file":"cv.pdf"}'),
        ("POST", "https://x.test/search", '{"query":"engineer"}'),
    ],
)
def test_page_reads_and_file_uploads_are_not_submissions(method, url, body):
    assert not SubmissionGuard().is_submission_request(method=method, url=url, body=body)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("text", "Apply now"),
        ("id_attr", "apply"),
        ("name_attr", "submit"),
        ("value_attr", "send"),
        ("aria_label", "Confirm"),
        ("title_attr", "Finish"),
    ],
)
def test_step_click_guard_checks_every_signal(field, value):
    values: dict[str, object] = {
        "text": "Continue",
        "role": "next",
        "type_attr": "button",
        "confidence": 0.95,
    }
    values[field] = value
    control = FormControl(control_id="#next", selector="#next", **values)  # type: ignore[arg-type]
    model = FormModel(session_id="s", url="https://x.test/form", is_final_step=False)
    with pytest.raises(SubmissionGuardError):
        SubmissionGuard(allow_step_navigation=True).assert_click_step_allowed(control, model)


def test_plain_continue_remains_a_permitted_step_control():
    control = FormControl(
        control_id="#next",
        text="Continue",
        role="next",
        type_attr="button",
        confidence=0.95,
        selector="#next",
    )
    model = FormModel(session_id="s", url="https://x.test/form", is_final_step=False)
    SubmissionGuard(allow_step_navigation=True).assert_click_step_allowed(control, model)


class RecordingPage:
    def __init__(self) -> None:
        self.session_id = "session-1"
        self.values: dict[str, str] = {}
        self.submissions = 0

    async def fill_text(self, selector: str, value: str) -> None:
        self.values[selector] = value

    async def select_option(self, selector: str, value: str) -> None:
        self.values[selector] = value

    async def choose_radio(self, selector: str, value: str) -> None:
        self.values[selector] = value

    async def set_checked(self, selector: str, checked: bool) -> None:
        self.values[selector] = "yes" if checked else "no"

    async def upload_file(self, selector: str, path: str) -> None:
        self.values[selector] = path

    async def read_value(self, selector: str) -> str:
        return self.values.get(selector, "")

    async def read_validation(self, selector: str) -> str | None:
        return None


@pytest.mark.asyncio
async def test_applying_the_same_plan_twice_is_idempotent():
    model = _inspect(
        '<label for="first_name">First name</label>'
        '<input id="first_name" name="first_name">'
    )
    filler = GenericFormFiller()
    plan = filler.plan(model, ANSWERS)
    page = RecordingPage()

    first = await filler.apply(page, plan)
    second = await filler.apply(page, plan)

    assert [item.field_id for item in first.filled] == [
        item.field_id for item in second.filled
    ]
    assert page.submissions == 0


def test_suppression_evaluation_is_stable_and_bounded_at_scale():
    history = [
        AppliedJob(
            application_id=f"app{i}",
            job_id=f"old{i}",
            company_id=f"co{i % 10}",
            title="Backend Engineer",
            title_family="engineering",
            description_text=BACKEND,
            canonical_url=f"https://example.test/jobs/old{i}",
            applied_at=NOW - timedelta(days=30),
            department="Platform",
            locations=({"city": "Berlin", "country": "DE"},),
            workplace_type="remote",
        )
        for i in range(10)
    ]
    candidates = [
        SuppressionCandidate(
            job_id=f"job{i}",
            company_id=f"co{i % 15}",
            title="Backend Engineer" if i % 3 else "Accountant",
            title_family="engineering" if i % 3 else "finance",
            description_text=BACKEND if i % 3 else ACCOUNTING,
            canonical_url=f"https://example.test/jobs/job{i}",
            department="Platform",
            locations=({"city": "Berlin", "country": "DE"},),
            workplace_type="remote",
        )
        for i in range(200)
    ]
    policy = SuppressionPolicy()
    started = time.perf_counter()
    first = [evaluate_suppression(candidate, history, policy, now=NOW) for candidate in candidates]
    second = [evaluate_suppression(candidate, history, policy, now=NOW) for candidate in candidates]
    elapsed = time.perf_counter() - started

    assert [decision.reason if decision else None for decision in first] == [
        decision.reason if decision else None for decision in second
    ]
    assert any(decision is not None for decision in first)
    assert all(
        decision is None or decision.application_id in {app.application_id for app in history}
        for decision in first
    )
    assert elapsed < 10


def test_answer_book_is_bounded_by_settings_validation():
    answers = {f"key{i}": "value" for i in range(257)}
    with pytest.raises(ValidationError):
        AppConfig().apply_patch({"autofill": {"answers": answers}})
    with pytest.raises(ValidationError):
        AppConfig().apply_patch({"autofill": {"answers": {"email": "x" * 5000}}})
    AppConfig().apply_patch({"autofill": {"answers": {"email": "ada@example.com"}}})
