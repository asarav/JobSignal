"""``partial``: never fails, records what little we know (M2).

The last strategy of the pipeline. It always succeeds so a posting we cannot
read still becomes a (partial) job record instead of disappearing: the spec
requires that unknown ATS pages still yield complete *or partial* records.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote, urlparse

from app.domain.models.extraction import ExtractionTarget
from app.plugins.extraction.base import StrategyBase

SLUG_RE = re.compile(r"[-_\s]+")

FIELD_CONFIDENCE = {"title": 0.15}


class PartialStrategy(StrategyBase):
    id = "extract.partial"
    default_order = 100

    def can_attempt(self, target: ExtractionTarget) -> bool:
        return True

    def fields(self, target: ExtractionTarget) -> dict[str, Any]:
        title = _title_from_url(target.url)
        if title:
            return {"title": title}
        return {"canonical_url": target.url}

    def confidence(self, field_name: str) -> float:
        return FIELD_CONFIDENCE.get(field_name, 0.1)


def _title_from_url(url: str) -> str | None:
    """Turn the last meaningful path segment into a readable title."""
    path = urlparse(url).path.rstrip("/")
    if not path:
        return None
    # Slugs with non-ASCII letters are percent-encoded in the URL.
    segment = unquote(path.split("/")[-1])
    if not segment or "." in segment and segment.count(".") > 1:
        return None
    # A numeric path segment is a posting id, not a readable job title. Calling
    # it a title hid the fact that the posting could not actually be read.
    if segment.isdigit():
        return None
    words = [word for word in SLUG_RE.split(segment) if word]
    if not words:
        return None
    return " ".join(word.capitalize() for word in words)
