"""Live Playwright run against the fixture site (spec §13.5.7, §19.5).

These tests need the optional browser extra and its Chromium build. They are the
only place a real browser is driven, and they are what the safety suite's
"zero submission requests" assertion means in practice: the fixture server
counts every POST, and only a deliberate human handoff is allowed to produce one.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.domain.models.browser import AnswerBook, OpenOptions
from app.domain.models.common import Err
from app.domain.services.form_filler import GenericFormFiller
from app.domain.services.form_inspector import inspect_html
from app.infrastructure.browser.playwright_provider import (
    PlaywrightBrowserProvider,
    playwright_available,
)
from tests.support.application_site import FixtureApplicationServer

pytestmark = pytest.mark.skipif(
    not playwright_available(),
    reason="Playwright is not installed (pip install 'lodestar[render]')",
)


def _answers(cv: Path) -> AnswerBook:
    return AnswerBook(
        answers={
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": "ada@example.com",
            "resume_file": str(cv),
        }
    )


async def _open(provider: PlaywrightBrowserProvider, url: str):
    outcome = await provider.open(url, OpenOptions(headed=False, timeout_s=30))
    if isinstance(outcome, Err):
        if "Executable doesn't exist" in outcome.error.message:
            pytest.skip("Chromium is not installed (python -m playwright install chromium)")
        raise outcome.error
    return outcome.value.session_id


async def _fill(provider: PlaywrightBrowserProvider, session_id: str, url: str, cv: Path):
    html = await provider.snapshot_dom(session_id)
    model = inspect_html(html, session_id=session_id, url=url)
    filler = GenericFormFiller()
    plan = filler.plan(model, _answers(cv))
    return await filler.apply(provider.handle(session_id), plan)


@pytest.mark.asyncio
async def test_autofill_never_submits_and_handoff_lets_the_human_submit(tmp_path):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fixture\n")
    provider = PlaywrightBrowserProvider()
    with FixtureApplicationServer() as site:
        url = f"{site.base_url}/autosubmit.html"
        try:
            session_id = await _open(provider, url)
            report = await _fill(provider, session_id, url, cv)

            assert report.submitted is False
            assert {item.field_label for item in report.filled} >= {"First name", "Email"}
            page = provider.page(session_id)
            assert await page.locator("#first_name").input_value() == "Ada"
            assert await page.locator("#certify").is_checked() is False
            assert await page.locator("#pronouns").input_value() == ""

            # The page's own oninput handler tried to POST /apply while the
            # automation was writing. The interceptor is armed for the whole
            # session, so it was aborted.
            await page.wait_for_timeout(300)
            assert site.submissions == []
            assert provider.blocked_requests >= 1
            assert provider.interceptor_active(session_id) is True

            # Handing the window over is the user's decision (§13.4.5): only
            # then does their own Submit click work.
            provider.handoff(session_id)
            assert provider.interceptor_active(session_id) is False
            async with page.expect_navigation():
                await page.locator("#submit_application").click()
            assert len(site.submissions) == 1
            assert site.submissions[0][0] == "/apply"
        finally:
            await provider.close_all()


@pytest.mark.asyncio
async def test_a_delayed_page_submission_is_still_blocked(tmp_path):
    """A handler that submits seconds after the write must not get through."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fixture\n")
    provider = PlaywrightBrowserProvider()
    with FixtureApplicationServer() as site:
        url = f"{site.base_url}/delayed.html"
        try:
            session_id = await _open(provider, url)
            await _fill(provider, session_id, url, cv)

            # The page fires its POST 2.5 s after the first input event, long
            # after any "automation window" would have closed.
            await provider.page(session_id).wait_for_timeout(3200)
            assert site.submissions == []
            assert provider.blocked_requests >= 1
            assert provider.interceptor_active(session_id) is True

            # Re-arming is idempotent, and handoff is still the only exit.
            provider.rearm(session_id)
            assert provider.interceptor_active(session_id) is True
            provider.handoff(session_id)
            async with provider.page(session_id).expect_navigation():
                await provider.page(session_id).locator("#submit_application").click()
            assert len(site.submissions) == 1
        finally:
            await provider.close_all()


@pytest.mark.asyncio
async def test_hostile_page_attributes_still_fill_the_right_field(tmp_path):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fixture\n")
    provider = PlaywrightBrowserProvider()
    with FixtureApplicationServer() as site:
        url = f"{site.base_url}/weird.html"
        try:
            session_id = await _open(provider, url)
            html = await provider.snapshot_dom(session_id)
            model = inspect_html(html, session_id=session_id, url=url)
            first = next(f for f in model.fields if f.label == "First name")
            assert '\\"' in first.selector

            filler = GenericFormFiller()
            report = await filler.apply(
                provider.handle(session_id), filler.plan(model, _answers(cv))
            )
            assert "First name" in {item.field_label for item in report.filled}
            page = provider.page(session_id)
            assert await page.locator("form input").nth(0).input_value() == "Ada"
            assert await page.locator("form input").nth(2).input_value() == ""
            assert site.submissions == []
        finally:
            await provider.close_all()


@pytest.mark.asyncio
async def test_a_radio_group_is_checked_and_read_back_as_its_checked_option(tmp_path):
    """Stored "yes" reaches the option valued "Yes", and read-back sees it."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fixture\n")
    provider = PlaywrightBrowserProvider()
    with FixtureApplicationServer() as site:
        url = f"{site.base_url}/choices.html"
        try:
            session_id = await _open(provider, url)
            html = await provider.snapshot_dom(session_id)
            model = inspect_html(html, session_id=session_id, url=url)
            filler = GenericFormFiller()
            plan = filler.plan(
                model, AnswerBook(answers={"work_authorization": "yes"})
            )
            report = await filler.apply(provider.handle(session_id), plan)

            assert report.errors == ()
            assert {item.field_label for item in report.filled} == {
                "Are you authorized to work in the United Kingdom?"
            }
            page = provider.page(session_id)
            assert await page.locator('input[name="auth"][value="Yes"]').is_checked() is True
            assert await page.locator('input[name="auth"][value="No"]').is_checked() is False
            # The group's value is the checked option, not its first radio.
            assert (
                await provider.handle(session_id).read_value('input[name="auth"]')
                == "Yes"
            )
            assert site.submissions == []
        finally:
            await provider.close_all()


@pytest.mark.asyncio
async def test_a_file_input_hidden_behind_a_styled_widget_receives_the_file(tmp_path):
    """The native input of an upload button is hidden, not unusable."""
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4 fixture\n")
    provider = PlaywrightBrowserProvider()
    with FixtureApplicationServer() as site:
        url = f"{site.base_url}/styled-upload.html"
        try:
            session_id = await _open(provider, url)
            html = await provider.snapshot_dom(session_id)
            model = inspect_html(html, session_id=session_id, url=url)
            resume = next(field for field in model.fields if field.kind == "file")
            assert resume.hidden is True

            filler = GenericFormFiller()
            report = await filler.apply(
                provider.handle(session_id),
                filler.plan(model, AnswerBook(answers={"resume_file": str(cv)})),
            )
            assert resume.field_id in {item.field_id for item in report.filled}
            page = provider.page(session_id)
            # Chromium reports the chosen file as C:\fakepath\cv.pdf.
            assert await page.locator("#resume").input_value() != ""
            assert site.submissions == []
        finally:
            await provider.close_all()


@pytest.mark.asyncio
async def test_login_wall_is_reported_instead_of_filled():
    provider = PlaywrightBrowserProvider()
    with FixtureApplicationServer() as site:
        try:
            outcome = await provider.open(
                f"{site.base_url}/login.html", OpenOptions(headed=False)
            )
            assert isinstance(outcome, Err)
            assert outcome.error.code == "auth_required"
            assert site.submissions == []
        finally:
            await provider.close_all()
