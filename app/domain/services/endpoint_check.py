"""Classify what an employer's application page answered (spec §9.3).

This is a pure function so the verifier's decisions are testable without a
network. It is deliberately conservative: wording and HTTP status are strong
evidence; the absence of an application form is only reported when the page also
never says "apply" anywhere. A login wall or a block describes our access, not
the posting, and is returned as such so it can never become negative evidence.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from app.domain.services.signals import CLOSURE_PATTERNS, NEGATION_MARKERS

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")
_FORM_RE = re.compile(r"<form\b", re.IGNORECASE)
_APPLY_RE = re.compile(
    r"(apply|bewerben|postuler|solicitar|application|bewerbung)", re.IGNORECASE
)

#: Paths that are a generic landing page rather than a specific posting.
_GENERIC_PATHS: frozenset[str] = frozenset(
    {"", "/", "/careers", "/careers/", "/jobs", "/jobs/", "/job", "/job/"}
)

#: HTTP statuses that mean "we could not see the page", never "the job is gone".
_BLOCKED_STATUSES: frozenset[int] = frozenset({403, 429, 503})
_ERROR_STATUSES: frozenset[int] = frozenset({408, 500, 502, 504})


def visible_text(html: str) -> str:
    without_code = _SCRIPT_STYLE_RE.sub(" ", html)
    return _SPACE_RE.sub(" ", _TAG_RE.sub(" ", without_code)).strip()


def _negated(folded: str, index: int, pattern: str) -> bool:
    if pattern.startswith("no longer"):
        return False
    window = folded[max(0, index - 24) : index + len(pattern)]
    return any(marker in window for marker in NEGATION_MARKERS)


def _is_generic_redirect(requested_url: str, final_url: str) -> bool:
    requested = urlparse(requested_url)
    final = urlparse(final_url)
    if not requested.path or not final.path:
        return False
    if final.path.rstrip("/") == requested.path.rstrip("/"):
        return False
    return final.path.rstrip("/") in _GENERIC_PATHS


def classify_endpoint(
    *,
    status: int | None,
    final_url: str = "",
    html: str = "",
    requested_url: str = "",
    timed_out: bool = False,
) -> str:
    """Return one of the endpoint states stored in history."""
    if timed_out or status is None:
        return "timeout"
    if status in {404, 410}:
        return "gone_404_410"
    if status == 401:
        return "auth_required"
    if status in _BLOCKED_STATUSES:
        return "blocked"
    if status in _ERROR_STATUSES or status >= 500:
        return "error"
    if not 200 <= status < 300:
        return "error"

    text = visible_text(html).casefold()
    for pattern in CLOSURE_PATTERNS:
        index = text.find(pattern)
        if index >= 0 and not _negated(text, index, pattern):
            return "closed_message"

    if requested_url and _is_generic_redirect(requested_url, final_url):
        return "generic_redirect"

    if html and not _FORM_RE.search(html) and not _APPLY_RE.search(text):
        return "form_unavailable"

    return "ok"


__all__ = ["classify_endpoint", "visible_text"]
