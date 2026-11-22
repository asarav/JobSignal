"""Browser and form models (spec §13.2).

The page handle is deliberately data-only here: capabilities live in
``app.domain.ports.browser`` and the provider implements them. Nothing in the
model can click, press or evaluate — there is no representation for it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

FIELD_KINDS: tuple[str, ...] = (
    "text",
    "email",
    "tel",
    "url",
    "number",
    "date",
    "textarea",
    "select",
    "radio_group",
    "checkbox",
    "file",
    "combobox",
    "unknown",
)

#: Classification groups and their fill policy (§13.3).
FIELD_CLASSES: tuple[str, ...] = (
    "fillable_profile",
    "user_supplied_answer",
    "blocked_demographic",
    "blocked_legal",
    "blocked_sensitive",
    "free_text_unknown",
    "unknown",
)

BLOCKED_CLASSES: tuple[str, ...] = (
    "blocked_demographic",
    "blocked_legal",
    "blocked_sensitive",
)

#: Minimum confidence before a profile value is written (§13.3).
MIN_FILL_CONFIDENCE = 0.85

_QUESTION_NOISE = re.compile(r"[^a-z0-9]+")


def question_key(text: str | None) -> str:
    """Normalise a question's text so the same question matches on any page.

    Two forms of the same question arrive with different punctuation and
    spacing ("Why us?" / "why us"), so a saved answer is compared on
    lower-cased alphanumerics only. What the user typed is never rewritten.
    """
    return _QUESTION_NOISE.sub(" ", (text or "").casefold()).strip()

PLAN_ACTIONS: tuple[str, ...] = ("fill", "skip", "block")


@dataclass(frozen=True, slots=True)
class OpenOptions:
    headed: bool = True
    timeout_s: float = 30.0


@dataclass(frozen=True, slots=True)
class BrowserPage:
    session_id: str
    url: str


@dataclass(frozen=True, slots=True)
class FieldOption:
    value: str
    label: str = ""
    selected: bool = False


@dataclass(frozen=True, slots=True)
class FieldClassification:
    field_class: str = "unknown"
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()
    #: Canonical answer-book key (``email``, ``first_name`` …); ``None`` when the
    #: field is blocked or unrecognised.
    key: str | None = None

    @property
    def is_blocked(self) -> bool:
        return self.field_class in BLOCKED_CLASSES


@dataclass(frozen=True, slots=True)
class FormField:
    field_id: str
    kind: str = "unknown"
    label: str | None = None
    accessible_name: str | None = None
    name_attr: str | None = None
    id_attr: str | None = None
    autocomplete_attr: str | None = None
    aria: Mapping[str, str] = field(default_factory=dict)
    description: str | None = None
    options: Sequence[FieldOption] = ()
    required: bool = False
    section_hint: str | None = None
    validation_message: str | None = None
    classification: FieldClassification = field(default_factory=FieldClassification)
    step_index: int = 0
    selector: str = ""
    placeholder: str | None = None
    #: Honeypot / CSS-hidden inputs are reported but never touched.
    hidden: bool = False


@dataclass(frozen=True, slots=True)
class FormControl:
    control_id: str
    text: str = ""
    role: str = "other"  # submit | next | back | other
    tag: str = "button"
    type_attr: str | None = None
    confidence: float = 0.0
    selector: str = ""
    #: The remaining signals the submit lexicon must match (§13.5.3).
    value_attr: str | None = None
    aria_label: str | None = None
    title_attr: str | None = None
    id_attr: str | None = None
    name_attr: str | None = None


@dataclass(frozen=True, slots=True)
class FormModel:
    session_id: str
    url: str
    fields: Sequence[FormField] = ()
    controls: Sequence[FormControl] = ()
    step_index: int = 0
    step_count: int = 1
    is_final_step: bool | None = None

    @property
    def submit_controls(self) -> tuple[FormControl, ...]:
        return tuple(control for control in self.controls if control.role == "submit")

    @property
    def next_controls(self) -> tuple[FormControl, ...]:
        return tuple(control for control in self.controls if control.role == "next")

    @property
    def back_controls(self) -> tuple[FormControl, ...]:
        return tuple(control for control in self.controls if control.role == "back")


@dataclass(frozen=True, slots=True)
class AnswerBook:
    """Only what the user explicitly stored — nothing is inferred (§13.3)."""

    answers: Mapping[str, str] = field(default_factory=dict)
    #: Free-text answers the user saved for an open question, keyed by the
    #: question's text (or, from earlier builds, an exact field id).
    snippets: Mapping[str, str] = field(default_factory=dict)

    def value_for(self, key: str | None) -> str | None:
        if not key:
            return None
        value = self.answers.get(key)
        return value if isinstance(value, str) and value.strip() else None

    def snippet_for(self, field_id: str, question: str | None = None) -> str | None:
        """The saved answer for this open question, if the user mapped one.

        A field-id key wins first because that is what earlier builds stored.
        Otherwise the question's own text is compared with every stored key,
        ignoring case, spacing and punctuation, so a saved answer follows the
        question across pages and inspections.
        """
        direct = self.snippets.get(field_id)
        if isinstance(direct, str) and direct.strip():
            return direct
        wanted = question_key(question)
        if not wanted:
            return None
        for key, value in self.snippets.items():
            if isinstance(value, str) and value.strip() and question_key(key) == wanted:
                return value
        return None


@dataclass(frozen=True, slots=True)
class PlannedFill:
    field_id: str
    field_label: str
    kind: str
    value: str
    action: str = "fill"
    reason: str = ""
    source: str = ""
    confidence: float = 0.0
    selector: str = ""
    #: Classification carried into ``apply`` so the guard re-checks the class
    #: rather than trusting the plan.
    field_class: str = ""


@dataclass(frozen=True, slots=True)
class FillPlan:
    plan_id: str
    session_id: str
    url: str = ""
    step_index: int = 0
    items: Sequence[PlannedFill] = ()

    @property
    def fills(self) -> tuple[PlannedFill, ...]:
        return tuple(item for item in self.items if item.action == "fill")

    @property
    def skipped(self) -> tuple[PlannedFill, ...]:
        return tuple(item for item in self.items if item.action == "skip")

    @property
    def blocked(self) -> tuple[PlannedFill, ...]:
        return tuple(item for item in self.items if item.action == "block")


@dataclass(frozen=True, slots=True)
class FillReport:
    plan_id: str
    filled: Sequence[PlannedFill] = ()
    skipped: Sequence[PlannedFill] = ()
    blocked: Sequence[PlannedFill] = ()
    errors: Sequence[str] = ()
    #: Always false: the filler has no capability that could change this.
    submitted: bool = False
