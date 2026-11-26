"""Is this page a posting, or an error page wearing a 200?

Server-rendered ATS boards answer **200 for a page that is not a posting**.
Measured while building the seed: JazzHR redirects an unknown tenant to its own
marketing site, and a retired Paylocity company id lands on
``/Recruiting/Jobs/JobNotFound`` -- both with a 200.  Handed to extraction as a
successful fetch, either becomes a job with no title and no description, which
is worse than a clear failure: the dead posting disappears inside a clean run
and the board looks healthy while contributing nothing.

The check is deliberately narrow.  Dead-posting wording and documents with no
readable text at all are unambiguous; heading and length heuristics are not, so
they are left to extraction, which knows what a posting looks like per tenant.
"""

from __future__ import annotations

import re

from app.domain.models.common import PortError

#: Wording only a dead or wrong page carries.  Kept specific: matching a bare
#: "not found" would reject postings that quote such text in their own body.
_GONE_MARKERS = (
    "job not found",
    "jobnotfound",
    "position not found",
    "posting not found",
    "job posting not found",
    "page not found",
    "404 not found",
    "no longer available",
    "no longer accepting applications",
    "position has been filled",
    "this job has been filled",
    "this posting has expired",
    "job has expired",
    "job you are looking for",
)

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")


def visible_text(html: str) -> str:
    """The page's readable text, without script, style or markup."""
    without_code = _SCRIPT_STYLE_RE.sub(" ", html)
    return _SPACE_RE.sub(" ", _TAG_RE.sub(" ", without_code)).strip()


def posting_problem(html: str) -> str | None:
    """Why this page is not a posting, or ``None`` when it might be one."""
    text = visible_text(html)
    if not text:
        return "page has no readable text"
    lowered = text.lower()
    for marker in _GONE_MARKERS:
        if marker in lowered:
            return f"page says {marker!r}"
    return None


def invalid_posting(connector_id: str, url: str, reason: str) -> PortError:
    """A settled failure for a page that answered but carries no posting.

    ``retryable`` is false on purpose: the same URL will answer the same way,
    so a retry only spends budget.  The application counts a failed posting
    fetch and moves on, so a dead posting never penalises the source.
    """
    return PortError(
        code="connector.invalid_response",
        message=f"{connector_id}: {reason} at {url}",
        retryable=False,
        details={"connector_id": connector_id, "url": url, "reason": reason},
    )
