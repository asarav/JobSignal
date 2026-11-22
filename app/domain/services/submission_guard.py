"""Defence in depth against ever submitting an application (spec §13.5).

Nothing in this module is configurable at runtime: ``allow_step_navigation`` is
the one setting, and it can only permit *next-step* navigation — never a
submit-shaped control, never Enter, never a submission request.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlparse

from app.domain.models.browser import FormControl, FormModel

DEFAULT_SUBMIT_LEXICON: tuple[str, ...] = (
    "submit",
    "submit application",
    "apply",
    "apply now",
    "send application",
    "finish",
    "finish application",
    "complete",
    "complete application",
    "confirm",
    "confirm and submit",
    "done",
    "review and submit",
    "send",
)

#: URL shapes that are submission endpoints, not page views.
SUBMISSION_URL_PATTERNS: tuple[str, ...] = (
    r"/applications?/?$",
    r"/applications?/submit",
    r"/applications?/complete",
    r"/apply/?$",
    r"/apply/(submit|complete)$",
    r"/submit/?$",
    r"/application/(submit|confirmation)",
    r"/jobs/\d+/apply",
    r"/careers/apply",
    r"/candidate/apply",
)

#: Body markers that only appear in a submission payload.
SUBMISSION_BODY_MARKERS: tuple[str, ...] = (
    "action=submit",
    "_action=submit",
    "submit=1",
    "commit=submit",
    "confirm_application=true",
)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH"})


class SubmissionGuardError(RuntimeError):
    """Raised when a requested action could submit an application."""


def _normalize(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


@dataclass(frozen=True, slots=True)
class SubmissionGuard:
    lexicon: Sequence[str] = field(default_factory=lambda: DEFAULT_SUBMIT_LEXICON)
    allow_step_navigation: bool = False
    min_step_confidence: float = 0.90

    def matches_lexicon(self, *texts: str | None) -> bool:
        """Whether any text names a control that could submit the form."""
        for text in texts:
            normalized = _normalize(text)
            if not normalized:
                continue
            for term in self.lexicon:
                token = _normalize(term)
                if not token:
                    continue
                if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", normalized):
                    return True
        return False

    def assert_fill_allowed(self, *, field_class: str, value: str, kind: str) -> None:
        """Reject writes the policy forbids, and newlines in single-line inputs."""
        if field_class.startswith("blocked"):
            raise SubmissionGuardError(f"refusing to fill a {field_class} field")
        if "\n" in value or "\r" in value:
            if kind != "textarea":
                raise SubmissionGuardError("newline in a single-line field")

    def assert_click_step_allowed(
        self, control: FormControl, model: FormModel
    ) -> None:
        """Permit only a non-final, high-confidence, non-submit "next" click."""
        if not self.allow_step_navigation:
            raise SubmissionGuardError("step navigation is disabled")
        if control.role != "next":
            raise SubmissionGuardError(f"control is not a step control: {control.role}")
        if (control.type_attr or "").lower() == "submit":
            raise SubmissionGuardError("step control is a submit button")
        if control.confidence < self.min_step_confidence:
            raise SubmissionGuardError("step control confidence too low")
        # Every signal §13.5.3 names, not just the visible text.
        if self.matches_lexicon(
            control.text,
            control.value_attr,
            control.aria_label,
            control.title_attr,
            control.id_attr,
            control.name_attr,
            control.control_id,
        ):
            raise SubmissionGuardError("step control matches the submit lexicon")
        if model.is_final_step is not False:
            # ``None`` means "cannot tell", which is not good enough.
            raise SubmissionGuardError("form may be on its final step")

    def is_submission_request(
        self,
        *,
        method: str,
        url: str,
        body: str = "",
        form_action: str | None = None,
    ) -> bool:
        """Whether a request would submit the application form."""
        if method.upper() in SAFE_METHODS:
            return False
        if method.upper() not in MUTATING_METHODS:
            return False
        parsed = urlparse(url)
        path = parsed.path.lower()
        if any(re.search(pattern, path) for pattern in SUBMISSION_URL_PATTERNS):
            return True
        if form_action and url.split("?", 1)[0] == form_action.split("?", 1)[0]:
            if "multipart/form-data" in body or "application/x-www-form-urlencoded" in body:
                return True
        lowered = body.lower()
        if any(marker in lowered for marker in SUBMISSION_BODY_MARKERS):
            return True
        # A form-encoded payload that carries the form's own fields is a post of
        # the application, even when the URL looks harmless.
        if form_action and _form_encoded(body) and parsed.netloc == urlparse(form_action).netloc:
            return True
        return False

    def assert_request_allowed(
        self,
        *,
        method: str,
        url: str,
        body: str = "",
        form_action: str | None = None,
    ) -> None:
        if self.is_submission_request(
            method=method, url=url, body=body, form_action=form_action
        ):
            raise SubmissionGuardError(f"blocked submission request {method} {url}")

    def assert_no_enter_key(self, key: str) -> None:
        if key.strip().lower() in {"enter", "numpadenter", "return"}:
            raise SubmissionGuardError("Enter would submit the form")


def _form_encoded(body: str) -> bool:
    """Whether a body looks like ``application/x-www-form-urlencoded`` data."""
    if not body or "=" not in body:
        return False
    pairs = parse_qsl(body, keep_blank_values=True)
    return len(pairs) >= 2
