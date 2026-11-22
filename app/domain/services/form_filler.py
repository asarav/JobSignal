"""Generic form filler: ``FormModel`` + ``AnswerBook`` → ``FillPlan`` (§13.4).

Planning is pure and conservative: every field gets an explicit action and
reason, so the review panel can show what will happen before anything is
touched. Applying only ever calls the narrow page primitives from
``app.domain.ports.browser``; there is no way to click, press or submit.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from app.domain.models.browser import (
    BLOCKED_CLASSES,
    MIN_FILL_CONFIDENCE,
    AnswerBook,
    FillPlan,
    FillReport,
    FormField,
    FormModel,
    PlannedFill,
)
from app.domain.services.submission_guard import SubmissionGuard, SubmissionGuardError

TRUTHY = frozenset({"yes", "true", "1", "on", "checked", "y"})
FALSY = frozenset({"no", "false", "0", "off", "unchecked", "n"})
#: A checkbox whose own option text is one of these *is* the answer, so the
#: stored answer picks which box to tick (see :func:`_yes_no_option`).
YES_NO_TOKENS = frozenset({"yes", "no"})


def _yes_no_option(form_field: FormField) -> frozenset[str] | None:
    """The yes/no tokens a checkbox carries as its own option text, if any.

    ``None`` for an ordinary checkbox ("I agree to the terms"): those keep the
    long-standing rule that only a stored yes can tick them. A yes/no pair that
    shares one name (Lever's "Are you eligible to work in the United States?")
    is different — each box is an answer, so only the box whose own label
    matches the stored answer is ticked, and a stored "no" ticks the "No" box
    instead of being ignored.
    """
    if not form_field.options:
        return None
    option = form_field.options[0]
    tokens = {
        token
        for token in (option.value.strip().casefold(), option.label.strip().casefold())
        if token
    }
    if not tokens or not tokens <= YES_NO_TOKENS:
        return None
    return frozenset(tokens)


#: Option names a skip reason may list before it is abbreviated; a stress-test
#: radio group can carry hundreds.
MAX_REASON_OPTIONS = 6


def _option_names(form_field: FormField) -> str:
    names = [
        (option.label or option.value).strip()
        for option in form_field.options
        if (option.label or option.value).strip()
    ]
    if len(names) > MAX_REASON_OPTIONS:
        return f"{', '.join(names[:MAX_REASON_OPTIONS])}, …"
    return ", ".join(names)


#: The only answer keys that may fill a hidden file input. ATS pages hide the
#: native file input behind a styled upload button, so hidden does not mean
#: honeypot for a *positively recognised* résumé or cover-letter upload.
STYLED_UPLOAD_KEYS = frozenset({"resume_file", "cover_letter_file"})


def _styled_file_upload(form_field: FormField) -> bool:
    """Whether a hidden input is a styled widget's résumé/cover-letter control.

    Only a file input the classifier named as the résumé or cover letter, at
    fill confidence, qualifies. An unrecognised hidden control keeps the
    blanket refusal, so nothing but the two upload fields can ever be written
    through a hidden element.
    """
    if form_field.kind != "file":
        return False
    classification = form_field.classification
    return (
        classification.field_class == "fillable_profile"
        and classification.key in STYLED_UPLOAD_KEYS
        and classification.confidence >= MIN_FILL_CONFIDENCE
    )


def _radio_option_value(form_field: FormField, answer: str) -> str | None:
    """The option a stored answer picks out, or ``None`` when it picks none.

    A radio can only be addressed by its ``value`` attribute, and matching that
    exactly made a stored "yes" miss an option whose value is "Yes" — then the
    browser call sat in a locator timeout. The options are known here, so the
    answer is resolved against them (value or label, case-insensitively) and an
    answer that matches nothing is skipped with a reason instead of forced at
    the page. Radios without a value attribute are all submitted as "on" and
    cannot be told apart, so they are never a candidate.
    """
    wanted = answer.strip().casefold()
    if not wanted:
        return None
    for option in form_field.options:
        if not option.value.strip():
            continue
        candidates = {
            option.value.strip().casefold(),
            (option.label or "").strip().casefold(),
        }
        if wanted in candidates:
            return option.value
    return None


class FillPage(Protocol):
    """The only capabilities the filler may use (spec §13.5 requirement 1)."""

    session_id: str

    async def fill_text(self, selector: str, value: str) -> None: ...

    async def select_option(self, selector: str, value: str) -> None: ...

    async def choose_radio(self, selector: str, value: str) -> None: ...

    async def set_checked(self, selector: str, checked: bool) -> None: ...

    async def upload_file(self, selector: str, path: str) -> None: ...

    async def read_value(self, selector: str) -> str: ...

    async def read_validation(self, selector: str) -> str | None: ...


@dataclass(frozen=True, slots=True)
class GenericFormFiller:
    guard: SubmissionGuard = field(default_factory=SubmissionGuard)
    min_confidence: float = MIN_FILL_CONFIDENCE
    #: Hard bound on writes per step, so a hostile page cannot turn one click
    #: into thousands of sequential browser actions.
    max_fills: int = 200
    #: Minimal pacing between writes (§13.4.4); ``0`` keeps pure tests instant.
    delay_s: float = 0.0

    def _plan_field(self, form_field: FormField, answers: AnswerBook) -> PlannedFill:
        classification = form_field.classification
        label = (
            form_field.label
            or form_field.accessible_name
            or form_field.name_attr
            or form_field.field_id
        )
        base: dict[str, Any] = {
            "field_id": form_field.field_id,
            "field_label": label,
            "kind": form_field.kind,
            "selector": form_field.selector,
            "confidence": classification.confidence,
            "field_class": classification.field_class,
        }

        if form_field.hidden and not _styled_file_upload(form_field):
            if form_field.kind == "file":
                # A hidden file input the classifier cannot name is a honeypot
                # or something the app has no business touching; a recognised
                # styled upload widget falls through to the normal upload path.
                return PlannedFill(
                    **base,
                    value="",
                    action="skip",
                    reason=(
                        "unrecognised hidden file input: attach the file yourself "
                        "in the browser"
                    ),
                )
            return PlannedFill(
                **base, value="", action="skip", reason="hidden or honeypot control"
            )
        if classification.field_class in BLOCKED_CLASSES:
            return PlannedFill(
                **base,
                value="",
                action="block",
                reason=f"{classification.field_class}: never filled automatically",
            )
        if classification.field_class == "free_text_unknown":
            snippet = answers.snippet_for(form_field.field_id, label)
            if snippet:
                return PlannedFill(
                    **base,
                    value=snippet,
                    action="fill",
                    reason="saved snippet the user mapped to this field",
                    source="snippet",
                )
            return PlannedFill(
                **base,
                value="",
                action="skip",
                reason="open question: answers are never generated",
            )
        if classification.field_class == "unknown":
            return PlannedFill(
                **base, value="", action="skip", reason="field not recognised"
            )

        value = answers.value_for(classification.key)
        if value is None:
            return PlannedFill(
                **base,
                value="",
                action="skip",
                reason="no stored answer",
            )

        if (
            classification.field_class == "fillable_profile"
            and classification.confidence < self.min_confidence
        ):
            return PlannedFill(
                **base,
                value="",
                action="skip",
                reason=(
                    "confidence below threshold "
                    f"({classification.confidence:.2f} < {self.min_confidence:.2f})"
                ),
            )

        if form_field.kind == "radio_group":
            option_value = _radio_option_value(form_field, value)
            if option_value is None:
                names = _option_names(form_field)
                return PlannedFill(
                    **base,
                    value="",
                    action="skip",
                    reason=(
                        "the stored answer matches none of these options"
                        + (f" ({names})" if names else "")
                        + "; pick one yourself"
                    ),
                )
            value = option_value

        if form_field.kind == "checkbox":
            option = _yes_no_option(form_field)
            if option is not None:
                answer = value.strip().casefold()
                if answer not in option:
                    return PlannedFill(
                        **base,
                        value="",
                        action="skip",
                        reason=(
                            "this option is not the stored answer "
                            f"({', '.join(sorted(option))})"
                        ),
                    )
                return PlannedFill(
                    **base,
                    value=answer,
                    action="fill",
                    reason=f"stored {classification.key}",
                    source=classification.key or "",
                )
            lowered = value.strip().lower()
            if lowered in FALSY:
                return PlannedFill(
                    **base,
                    value="",
                    action="skip",
                    reason="stored answer is negative; leaving the box as it is",
                )
            if lowered not in TRUTHY:
                return PlannedFill(
                    **base,
                    value="",
                    action="skip",
                    reason="checkbox answer is not a yes/no value",
                )
            value = "yes"

        return PlannedFill(
            **base,
            value=value,
            action="fill",
            reason=f"stored {classification.key}",
            source=classification.key or "",
        )

    def plan(
        self,
        form: FormModel,
        answers: AnswerBook,
        *,
        plan_id: str | None = None,
    ) -> FillPlan:
        budget = self.max_fills
        items: list[PlannedFill] = []
        for form_field in form.fields:
            item = self._plan_field(form_field, answers)
            if item.action == "fill":
                if budget <= 0:
                    item = replace(
                        item,
                        value="",
                        action="skip",
                        reason="over the per-step autofill limit",
                        source="",
                    )
                else:
                    budget -= 1
            items.append(item)
        return FillPlan(
            plan_id=plan_id or uuid.uuid4().hex,
            session_id=form.session_id,
            url=form.url,
            step_index=form.step_index,
            items=tuple(items),
        )

    async def apply(self, page: FillPage, plan: FillPlan) -> FillReport:
        filled: list[PlannedFill] = []
        errors: list[str] = []
        for position, item in enumerate(plan.fills):
            if position and self.delay_s > 0:
                await asyncio.sleep(self.delay_s)
            try:
                # Fail closed: anything the guard refuses is reported, not forced.
                self.guard.assert_fill_allowed(
                    field_class=item.field_class or "fillable_profile",
                    value=item.value,
                    kind=item.kind,
                )
            except SubmissionGuardError as exc:
                errors.append(f"{item.field_label}: {exc}")
                continue
            try:
                await self._write(page, item)
            except Exception as exc:  # noqa: BLE001 - one bad field must not stop the rest
                errors.append(f"{item.field_label}: {exc}")
                continue
            try:
                current = await page.read_value(item.selector)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{item.field_label}: read-back failed: {exc}")
                continue
            if not _matches(item, current):
                validation = None
                try:
                    validation = await page.read_validation(item.selector)
                except Exception:  # noqa: BLE001 - validation text is best effort
                    validation = None
                errors.append(
                    f"{item.field_label}: value not accepted"
                    + (f" ({validation})" if validation else "")
                )
                continue
            filled.append(item)
        # A controlled input or a typeahead can accept a write and then put its
        # own value back — Lever's location widget clears itself on blur unless
        # a suggestion was picked. Read the filled fields once more at the end
        # of the pass, so "filled" means the page still holds the value.
        if filled and self.delay_s > 0:
            await asyncio.sleep(self.delay_s)
        kept: list[PlannedFill] = []
        for item in filled:
            try:
                current = await page.read_value(item.selector)
            except Exception:  # noqa: BLE001 - a failed re-read is not a lost value
                kept.append(item)
                continue
            if _matches(item, current):
                kept.append(item)
            else:
                errors.append(
                    f"{item.field_label}: the page did not keep the value "
                    "(pick it yourself in the browser)"
                )
        return FillReport(
            plan_id=plan.plan_id,
            filled=tuple(kept),
            skipped=plan.skipped,
            blocked=plan.blocked,
            errors=tuple(errors),
            submitted=False,
        )

    async def _write(self, page: FillPage, item: PlannedFill) -> None:
        if item.kind == "select":
            await page.select_option(item.selector, item.value)
        elif item.kind == "combobox":
            await page.select_option(item.selector, item.value)
        elif item.kind == "radio_group":
            await page.choose_radio(item.selector, item.value)
        elif item.kind == "checkbox":
            await page.set_checked(item.selector, True)
        elif item.kind == "file":
            await page.upload_file(item.selector, item.value)
        else:
            await page.fill_text(item.selector, item.value)


def _matches(item: PlannedFill, current: Any) -> bool:
    if current is None:
        return False
    if item.kind == "checkbox":
        token = str(current).strip().casefold()
        expected = item.value.strip().casefold()
        # A yes/no box reads back as its own value ("Yes"/"No"), so the answer
        # itself is the check. An ordinary checked box reads back as "on".
        if token == expected:
            return True
        return expected in TRUTHY and token in TRUTHY | {"checked"}
    expected = item.value.strip()
    actual = str(current).strip()
    if item.kind == "file":
        return bool(actual) and actual.split("/")[-1].split("\\")[-1] == expected.split(
            "/"
        )[-1].split("\\")[-1]
    return expected == actual
