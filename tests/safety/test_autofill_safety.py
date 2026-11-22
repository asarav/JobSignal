"""Autofill safety suite (spec §19.5 requirements 1–5).

These tests are CI-blocking. They assert that no scenario can submit an
application, tick a consent or demographic control, or touch a honeypot — and
that the code has no API with which it could even try.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from app.application.dto import BrowserHandoffResult
from app.domain.models.browser import BLOCKED_CLASSES, AnswerBook, FormModel
from app.domain.models.common import PortError
from app.domain.ports.browser import AutofillPage
from app.domain.services.form_filler import GenericFormFiller
from app.domain.services.form_inspector import inspect_html
from app.domain.services.submission_guard import (
    SubmissionGuard,
    SubmissionGuardError,
)
from app.worker.ipc.method_catalog import MILESTONE_METHOD_NAMES

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "application_site"

ANSWERS = AnswerBook(
    answers={
        "first_name": "Ada",
        "last_name": "Lovelace",
        "full_name": "Ada Lovelace",
        "email": "ada@example.com",
        "phone": "+44 20 0000 0000",
        "linkedin_url": "https://linkedin.com/in/ada",
        "resume_file": "/home/ada/cv.pdf",
        "years_experience": "9",
    }
)


class RecordingPage:
    """A page double that records every call and counts submissions."""

    def __init__(self) -> None:
        self.session_id = "session-1"
        self.values: dict[str, str] = {}
        self.filled: list[tuple[str, str]] = []
        self.selected: list[tuple[str, str]] = []
        self.checked: list[tuple[str, bool]] = []
        self.uploads: list[tuple[str, str]] = []
        self.submissions = 0
        self.clicks: list[str] = []
        self.keys: list[str] = []

    async def fill_text(self, selector: str, value: str) -> None:
        self.values[selector] = value
        self.filled.append((selector, value))

    async def select_option(self, selector: str, value: str) -> None:
        self.values[selector] = value
        self.selected.append((selector, value))

    async def choose_radio(self, selector: str, value: str) -> None:
        self.values[selector] = value
        self.selected.append((selector, value))

    async def set_checked(self, selector: str, checked: bool) -> None:
        self.values[selector] = "yes" if checked else "no"
        self.checked.append((selector, checked))

    async def upload_file(self, selector: str, path: str) -> None:
        self.values[selector] = path
        self.uploads.append((selector, path))

    async def read_value(self, selector: str) -> str:
        return self.values.get(selector, "")

    async def read_validation(self, selector: str) -> str | None:
        return None


def _model(name: str) -> FormModel:
    html = (FIXTURES / name).read_text(encoding="utf-8")
    return inspect_html(html, session_id="session-1", url=f"https://example.test/{name}")


def _model_html(html: str) -> FormModel:
    return inspect_html(html, session_id="session-1", url="https://example.test/apply")


#: Lever's card-style question: the question text is a sibling of the control
#: list, so each box's own label can only say "Yes" or "No".
CARD_CHOICES = """
<form action="/apply" method="post">
  <div class="question-card">
    <div class="application-label">
      <div class="text">Are you eligible to work in the United States?</div>
    </div>
    <div class="application-field">
      <label><input type="checkbox" name="cards[1][field0]" value="Yes"><span>Yes</span></label>
      <label><input type="checkbox" name="cards[1][field0]" value="No"><span>No</span></label>
    </div>
  </div>
</form>
"""

def _fields_by_label(model: FormModel) -> dict[str, object]:
    out: dict[str, object] = {}
    for form_field in model.fields:
        label = form_field.label or form_field.accessible_name or form_field.field_id
        out[label] = form_field
    return out


FIXTURE_FILES = ("generic.html", "multistep.html", "adversarial.html")


@pytest.mark.parametrize("name", FIXTURE_FILES)
@pytest.mark.asyncio
async def test_no_fixture_scenario_submits_or_ticks_consent(name):
    model = _model(name)
    page = RecordingPage()
    filler = GenericFormFiller()
    plan = filler.plan(model, ANSWERS)
    report = await filler.apply(page, plan)

    assert page.submissions == 0
    assert report.submitted is False
    # No legal, demographic or sensitive field is ever in the fill plan.
    for item in plan.fills:
        assert item.action == "fill"
    blocked_ids = {item.field_id for item in plan.blocked}
    assert blocked_ids.isdisjoint({item.field_id for item in plan.fills})
    assert all(checked for _, checked in page.checked)
    for selector, _ in page.checked:
        assert selector not in {
            form_field.selector
            for form_field in model.fields
            if form_field.classification.is_blocked
        }


@pytest.mark.parametrize("name", FIXTURE_FILES)
def test_blocked_fields_are_never_planned_for_filling(name):
    model = _model(name)
    plan = GenericFormFiller().plan(model, ANSWERS)
    blocked_ids = {
        form_field.field_id
        for form_field in model.fields
        if form_field.classification.field_class in BLOCKED_CLASSES
    }
    fill_ids = {item.field_id for item in plan.fills}
    assert fill_ids.isdisjoint(blocked_ids)
    for item in plan.items:
        if item.field_id in blocked_ids:
            assert item.action == "block"


def test_generic_fixture_classification_is_conservative():
    model = _model("generic.html")
    by_label = _fields_by_label(model)
    assert by_label["First name"].classification.key == "first_name"
    assert by_label["Email"].classification.key == "email"
    assert by_label["Resume/CV"].classification.key == "resume_file"
    assert by_label["Cover letter"].classification.field_class == "free_text_unknown"
    assert by_label["Gender"].classification.field_class == "blocked_demographic"
    assert by_label["Race/ethnicity"].classification.field_class == "blocked_demographic"
    assert by_label["Veteran status"].classification.field_class == "blocked_demographic"
    assert by_label["Disability status"].classification.field_class == "blocked_demographic"
    assert by_label["Social security number"].classification.field_class == (
        "blocked_sensitive"
    )
    assert by_label[
        "Are you authorized to work in this country?"
    ].classification.field_class == "user_supplied_answer"
    assert any(
        form_field.classification.field_class == "blocked_legal"
        for form_field in model.fields
    )
    # The submit control is named, and never exposed as a fill target.
    assert model.submit_controls
    assert model.is_final_step is True


def test_consent_checkbox_is_blocked_and_never_checked():
    model = _model("generic.html")
    certify = next(
        form_field for form_field in model.fields if form_field.name_attr == "certify"
    )
    assert certify.classification.field_class == "blocked_legal"
    plan = GenericFormFiller().plan(model, AnswerBook(answers={"certify": "yes"}))
    item = next(entry for entry in plan.items if entry.field_id == certify.field_id)
    assert item.action == "block"
    assert plan.fills == ()


def test_honeypot_is_reported_but_never_filled():
    model = _model("generic.html")
    honeypot = next(
        form_field
        for form_field in model.fields
        if form_field.name_attr == "website_confirm"
    )
    assert honeypot.hidden is True
    filler = GenericFormFiller()
    plan = filler.plan(model, AnswerBook(answers={"website_confirm": "trap"}))
    item = next(entry for entry in plan.items if entry.field_id == honeypot.field_id)
    assert item.action == "skip"
    assert "honeypot" in item.reason


@pytest.mark.asyncio
async def test_profile_values_are_filled_and_read_back():
    model = _model("generic.html")
    page = RecordingPage()
    filler = GenericFormFiller()
    report = await filler.apply(page, filler.plan(model, ANSWERS))

    filled_labels = {item.field_label for item in report.filled}
    assert {"First name", "Last name", "Email", "Phone", "Resume/CV"} <= filled_labels
    assert ("/home/ada/cv.pdf") in [path for _, path in page.uploads]
    assert report.errors == ()
    assert page.submissions == 0


@pytest.mark.asyncio
async def test_user_supplied_answers_are_only_filled_when_stored():
    model = _model("generic.html")
    auth = next(
        form_field
        for form_field in model.fields
        if form_field.name_attr == "work_authorization"
    )
    filler = GenericFormFiller()

    without = filler.plan(model, ANSWERS)
    item = next(entry for entry in without.items if entry.field_id == auth.field_id)
    assert item.action == "skip"
    assert "no stored answer" in item.reason

    with_answer = filler.plan(model, AnswerBook(answers={"work_authorization": "yes"}))
    item = next(entry for entry in with_answer.items if entry.field_id == auth.field_id)
    assert item.action == "fill"
    assert item.value == "yes"


def test_yes_no_card_answer_only_ticks_the_matching_box():
    """A stored answer picks one box; the other option is never ticked."""
    model = _model_html(CARD_CHOICES)
    filler = GenericFormFiller()

    def ticked(answer: str | None) -> list[str]:
        book = AnswerBook(answers={"work_authorization": answer} if answer else {})
        plan = filler.plan(model, book)
        return [item.selector for item in plan.fills if item.kind == "checkbox"]

    assert ticked("yes") == ['[name="cards[1][field0]"][value="Yes"]']
    assert ticked("no") == ['[name="cards[1][field0]"][value="No"]']
    # Nothing stored means nothing ticked, and no answer is invented.
    assert ticked(None) == []


#: The same card question as radio buttons, with an option list that also
#: includes an option whose value is not its visible text.
RADIO_CHOICES = """
<form action="/apply" method="post">
  <div class="question-card">
    <div class="application-label">
      <div class="text">Are you authorized to work in the United Kingdom?</div>
    </div>
    <div class="application-field">
      <label><input type="radio" name="auth" value="Yes"><span>Yes</span></label>
      <label><input type="radio" name="auth" value="No"><span>No</span></label>
    </div>
  </div>
</form>
"""

#: Value and label differ, which is common when a form posts codes.
LABELLED_RADIO_CHOICES = """
<form action="/apply" method="post">
  <div class="question-card">
    <div class="application-label">
      <div class="text">Are you authorized to work in the United Kingdom?</div>
    </div>
    <div class="application-field">
      <label><input type="radio" name="auth" value="auth_yes"><span>Yes</span></label>
      <label><input type="radio" name="auth" value="auth_no"><span>No</span></label>
    </div>
  </div>
</form>
"""


def test_a_radio_group_takes_the_option_value_the_stored_answer_names():
    """A stored "yes" has to reach an option whose value is "Yes"."""
    filler = GenericFormFiller()

    def chosen(html: str, answer: str) -> tuple[str, str]:
        plan = filler.plan(_model_html(html), AnswerBook(answers={"work_authorization": answer}))
        item = next(entry for entry in plan.items if entry.kind == "radio_group")
        return item.action, item.value

    # Case is ignored, and the option's own value is what is written.
    assert chosen(RADIO_CHOICES, "yes") == ("fill", "Yes")
    assert chosen(RADIO_CHOICES, "No") == ("fill", "No")
    # An option whose label is the answer is selected by its value.
    assert chosen(LABELLED_RADIO_CHOICES, "yes") == ("fill", "auth_yes")


@pytest.mark.asyncio
async def test_a_checked_radio_is_read_back_as_the_chosen_option():
    model = _model_html(RADIO_CHOICES)
    page = RecordingPage()
    filler = GenericFormFiller()
    report = await filler.apply(
        page, filler.plan(model, AnswerBook(answers={"work_authorization": "yes"}))
    )

    assert page.selected == [('input[name="auth"]', "Yes")]
    assert [item.field_label for item in report.filled] == [
        "Are you authorized to work in the United Kingdom?"
    ]
    assert report.errors == ()
    assert report.submitted is False


def test_a_radio_group_with_no_matching_option_is_skipped_not_forced():
    """A value the page does not offer is never written; the user picks."""
    model = _model_html(RADIO_CHOICES)
    plan = GenericFormFiller().plan(model, AnswerBook(answers={"work_authorization": "maybe"}))
    item = next(entry for entry in plan.items if entry.kind == "radio_group")

    assert item.action == "skip"
    assert item.value == ""
    assert "Yes, No" in item.reason
    assert plan.fills == ()


class ResettingPage(RecordingPage):
    """Mimics a typeahead that puts its own value back when the field blurs."""

    async def fill_text(self, selector: str, value: str) -> None:
        for earlier in list(self.values):
            if earlier != selector:
                self.values[earlier] = ""
        await super().fill_text(selector, value)


@pytest.mark.asyncio
async def test_a_value_the_page_discards_is_not_reported_as_filled():
    model = _model_html(
        """
        <form action="/apply" method="post">
          <label>Email <input id="email" name="email" type="email"></label>
          <label>Phone <input id="phone" name="phone" type="tel"></label>
        </form>
        """
    )
    page = ResettingPage()
    filler = GenericFormFiller()
    report = await filler.apply(page, filler.plan(model, ANSWERS))

    assert [item.field_label for item in report.filled] == ["Phone"]
    assert any("did not keep the value" in error for error in report.errors)
    assert report.submitted is False


def test_a_profile_location_is_filled_from_the_answer_book():
    model = _model_html(
        """
        <form action="/apply" method="post">
          <label>Current location
            <input id="location-input" name="location" type="text">
          </label>
        </form>
        """
    )
    filler = GenericFormFiller()
    item = next(
        entry
        for entry in filler.plan(
            model, AnswerBook(answers={"location": "London, United Kingdom"})
        ).items
        if entry.field_label == "Current location"
    )
    assert item.action == "fill"
    assert item.value == "London, United Kingdom"


def test_an_age_range_question_is_never_answered_even_if_stored():
    """"Age range" is voluntary self-identification, like gender and race."""
    model = _model_html(
        """
        <form action="/apply" method="post">
          <li class="application-question">
            <div>
              <div class="application-label">
                <div class="text">What is your age range?</div>
              </div>
              <div class="application-field">
                <label><input type="radio" name="age" value="21-29"><span>21-29</span></label>
                <label><input type="radio" name="age" value="30-39"><span>30-39</span></label>
              </div>
            </div>
          </li>
        </form>
        """
    )
    plan = GenericFormFiller().plan(
        model, AnswerBook(answers={"age_range": "30-39"})
    )
    assert plan.fills == ()
    for item in plan.items:
        if item.kind == "radio_group":
            assert item.action == "block"
            assert item.field_class == "blocked_demographic"


#: A styled upload button whose native file input CSS hides.
STYLED_UPLOAD_QUESTION = """
<form action="/apply" method="post">
  <label class="upload-button">Attach your resume
    <input type="file" name="resume" class="invisible-resume-upload">
  </label>
</form>
"""


@pytest.mark.asyncio
async def test_a_styled_upload_widget_is_filled_from_the_stored_file():
    """A hidden input the classifier names as the résumé is still uploadable."""
    model = _model_html(STYLED_UPLOAD_QUESTION)
    resume = next(field for field in model.fields if field.kind == "file")
    assert resume.hidden is True
    assert resume.classification.key == "resume_file"
    page = RecordingPage()
    filler = GenericFormFiller()
    plan = filler.plan(
        model, AnswerBook(answers={"resume_file": "/home/ada/cv.pdf"})
    )
    item = next(entry for entry in plan.items if entry.field_id == resume.field_id)
    assert item.action == "fill"
    await filler.apply(page, plan)
    assert page.uploads == [(resume.selector, "/home/ada/cv.pdf")]
    assert page.submissions == 0
    assert page.clicks == []


def test_an_unrecognised_hidden_file_input_is_never_touched():
    """Only a positively recognised résumé/cover-letter upload gets through."""
    model = _model_html(
        """
        <form action="/apply" method="post">
          <input type="file" name="field_9" class="honeypot" aria-hidden="true">
        </form>
        """
    )
    field = next(entry for entry in model.fields if entry.kind == "file")
    assert field.hidden is True
    plan = GenericFormFiller().plan(
        model, AnswerBook(answers={"resume_file": "/home/ada/cv.pdf"})
    )
    item = next(entry for entry in plan.items if entry.field_id == field.field_id)
    assert item.action == "skip"
    assert "attach the file yourself" in item.reason


OPEN_QUESTION = """
<form action="/apply" method="post">
  <label for="why">Why do you want to work here?</label>
  <textarea id="why" name="why"></textarea>
</form>
"""


def test_a_saved_answer_is_typed_into_the_open_question_it_names():
    """The user's own saved answer is used; case and punctuation do not matter."""
    model = _model_html(OPEN_QUESTION)
    plan = GenericFormFiller().plan(
        model,
        AnswerBook(
            snippets={"why do you want to work here": "Because I build careful software."}
        ),
    )
    item = next(entry for entry in plan.items if entry.kind == "textarea")
    assert item.action == "fill"
    assert item.source == "snippet"
    assert item.value == "Because I build careful software."


def test_a_saved_answer_for_a_different_question_is_not_typed():
    model = _model_html(OPEN_QUESTION)
    plan = GenericFormFiller().plan(
        model, AnswerBook(snippets={"Tell us about a project": "A long story."})
    )
    item = next(entry for entry in plan.items if entry.kind == "textarea")
    assert item.action == "skip"
    assert "open question" in item.reason


def test_a_saved_answer_cannot_unlock_a_blocked_question():
    """Matching by question text never overrides a blocked class."""
    model = _model_html(
        """
        <form action="/apply" method="post">
          <label for="gender">What is your gender?</label>
          <textarea id="gender" name="gender"></textarea>
        </form>
        """
    )
    plan = GenericFormFiller().plan(
        model, AnswerBook(snippets={"What is your gender?": "Female"})
    )
    item = next(entry for entry in plan.items if entry.kind == "textarea")
    assert item.action == "block"
    assert item.field_class == "blocked_demographic"
    assert plan.fills == ()


def test_a_snippet_keyed_by_field_id_still_fills():
    """Earlier builds stored snippets by field id; those keep working."""
    model = _model_html(OPEN_QUESTION)
    field = next(entry for entry in model.fields if entry.kind == "textarea")
    plan = GenericFormFiller().plan(
        model, AnswerBook(snippets={field.field_id: "A stored answer."})
    )
    assert [item.value for item in plan.fills] == ["A stored answer."]


def test_adversarial_labels_do_not_unlock_blocked_fields():
    model = _model("adversarial.html")
    by_label = _fields_by_label(model)
    # A demographic id under a benign label stays blocked.
    gender = next(
        form_field for form_field in model.fields if form_field.id_attr == "gender"
    )
    assert gender.classification.field_class == "blocked_demographic"
    # A consent sentence that also mentions a first name stays legal, not fillable.
    consent = next(
        form_field
        for form_field in model.fields
        if form_field.id_attr == "first_name_consent"
    )
    assert consent.classification.field_class == "blocked_legal"
    assert by_label["Pronouns"].classification.field_class == "blocked_demographic"
    # A name field whose *attribute* says "submit" is still a name field.
    submit_note = next(
        form_field for form_field in model.fields if form_field.id_attr == "submit_note"
    )
    assert submit_note.classification.key == "full_name"
    # The tempting "Apply now" is classified as submit, never as next.
    assert any(control.text == "Apply now" and control.role == "submit"
               for control in model.controls)
    plan = GenericFormFiller().plan(model, ANSWERS)
    assert all(item.action != "fill" for item in plan.items if item.value == "")


def test_filler_plan_never_targets_a_submit_control():
    for name in FIXTURE_FILES:
        model = _model(name)
        plan = GenericFormFiller().plan(model, ANSWERS)
        control_selectors = {control.selector for control in model.controls}
        assert control_selectors.isdisjoint({item.selector for item in plan.fills})


def test_guard_rejects_submission_shaped_clicks():
    guard = SubmissionGuard(allow_step_navigation=False)
    model = _model("multistep.html")
    next_control = model.next_controls[0]
    with pytest.raises(SubmissionGuardError):
        guard.assert_click_step_allowed(next_control, model)

    enabled = SubmissionGuard(allow_step_navigation=True)
    enabled.assert_click_step_allowed(next_control, model)

    final = _model("generic.html")
    with pytest.raises(SubmissionGuardError):
        enabled.assert_click_step_allowed(final.submit_controls[0], final)

    sneaky = next(
        control
        for control in _model("adversarial.html").controls
        if control.text == "Apply now"
    )
    with pytest.raises(SubmissionGuardError):
        enabled.assert_click_step_allowed(sneaky, _model("adversarial.html"))


def test_guard_rejects_next_control_on_a_possibly_final_step():
    guard = SubmissionGuard(allow_step_navigation=True)
    model = _model("multistep.html")
    undecidable = FormModel(
        session_id=model.session_id,
        url=model.url,
        fields=model.fields,
        controls=model.controls,
        is_final_step=None,
    )
    with pytest.raises(SubmissionGuardError):
        guard.assert_click_step_allowed(model.next_controls[0], undecidable)


def test_guard_blocks_enter_and_newlines():
    guard = SubmissionGuard()
    with pytest.raises(SubmissionGuardError):
        guard.assert_no_enter_key("Enter")
    with pytest.raises(SubmissionGuardError):
        guard.assert_no_enter_key("NumpadEnter")
    with pytest.raises(SubmissionGuardError):
        guard.assert_fill_allowed(field_class="fillable_profile", value="a\nb", kind="text")
    guard.assert_fill_allowed(
        field_class="fillable_profile", value="line\nline", kind="textarea"
    )
    with pytest.raises(SubmissionGuardError):
        guard.assert_fill_allowed(
            field_class="blocked_legal", value="yes", kind="checkbox"
        )


def test_network_guard_blocks_submission_requests():
    guard = SubmissionGuard()
    assert guard.is_submission_request(
        method="POST", url="https://boards.example/apply", body="first_name=Ada&last_name=L"
    )
    assert guard.is_submission_request(
        method="POST", url="https://x.test/applications/123/submit", body="{}"
    )
    assert guard.is_submission_request(
        method="POST",
        url="https://x.test/form",
        body="action=submit&first_name=Ada",
    )
    assert guard.is_submission_request(
        method="PUT",
        url="https://x.test/step",
        body="multipart/form-data; boundary=1",
        form_action="https://x.test/step",
    )
    # Reading pages and uploading to object storage are not submissions.
    assert not guard.is_submission_request(method="GET", url="https://x.test/apply")
    assert not guard.is_submission_request(
        method="POST", url="https://storage.test/upload", body='{"file":"cv.pdf"}'
    )


def test_allowlist_has_no_submit_or_click_method():
    assert "browser.submit" not in MILESTONE_METHOD_NAMES
    assert "browser.click" not in MILESTONE_METHOD_NAMES
    assert not any(
        method.startswith(("browser.submit", "browser.click"))
        for method in MILESTONE_METHOD_NAMES
    )


def test_page_capabilities_have_no_generic_click_press_or_evaluate():
    forbidden = {"click", "press", "evaluate", "dispatch_event", "submit", "type"}
    exposed = set(dir(AutofillPage))
    assert forbidden.isdisjoint(exposed)
    for capability in ("fill_text", "select_option", "set_checked", "choose_radio"):
        assert capability in exposed


def test_interceptor_is_armed_for_a_sessions_whole_life():
    """No time-boxed window: a delayed page submission must stay blocked."""
    from app.infrastructure.browser import playwright_provider

    # A short post-fill window was tried and rejected: a page that posts two
    # seconds after the write slipped through (ADR 0008).
    assert not hasattr(playwright_provider, "AUTOMATION_GRACE_S")
    assert not hasattr(playwright_provider.PlaywrightBrowserProvider, "begin_automation")
    provider = playwright_provider.PlaywrightBrowserProvider()
    assert provider.interceptor_active("ghost") is False
    with pytest.raises(PortError):
        provider.handoff("ghost")


def test_handoff_is_the_only_guard_exit_and_cannot_submit():
    assert "browser.handoff" in MILESTONE_METHOD_NAMES
    result = BrowserHandoffResult(session_id="s")
    assert result.submitted is False
    assert result.interceptor_active is False


FORBIDDEN_API = re.compile(
    r"(\.click\(|\.press\(|requestSubmit\(|dispatchEvent\(|\.submit\(|page\.evaluate\()"
)
SCAN_TARGETS = (
    ROOT / "app" / "domain" / "services" / "form_inspector.py",
    ROOT / "app" / "domain" / "services" / "form_filler.py",
    ROOT / "app" / "domain" / "services" / "submission_guard.py",
    ROOT / "app" / "domain" / "ports" / "browser.py",
    ROOT / "app" / "infrastructure" / "browser",
)


def test_static_scan_finds_no_forbidden_browser_apis():
    violations: list[str] = []
    for target in SCAN_TARGETS:
        paths = [target] if target.is_file() else sorted(target.rglob("*.py"))
        for path in paths:
            text = path.read_text(encoding="utf-8")
            for match in FORBIDDEN_API.finditer(text):
                violations.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
    assert violations == [], violations
