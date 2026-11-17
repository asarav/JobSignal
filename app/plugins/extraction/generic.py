"""``generic``: last-resort heuristics for an unknown careers page (M2).

Nothing here is ATS-specific: Open Graph metadata, the first heading and the
longest visible text block. The confidence is deliberately low so a structured
strategy always wins a merge against it.
"""

from __future__ import annotations

from typing import Any

from app.domain.models.extraction import ExtractionTarget
from app.infrastructure.html import parsing
from app.plugins.extraction.base import StrategyBase

FIELD_CONFIDENCE = {
    "title": 0.4,
    "description_text": 0.35,
    "company_name": 0.3,
}

# Text blocks shorter than this are navigation noise, not a job description.
MIN_DESCRIPTION_LENGTH = 120


class GenericStrategy(StrategyBase):
    id = "extract.generic"
    default_order = 50

    def can_attempt(self, target: ExtractionTarget) -> bool:
        return bool(target.html)

    def fields(self, target: ExtractionTarget) -> dict[str, Any]:
        html = target.html or ""
        found: dict[str, Any] = {}

        meta = parsing.meta_tags(html)
        title = meta.get("og:title") or meta.get("twitter:title")
        if not title:
            for heading in parsing.headings(html):
                if heading.text.strip():
                    title = heading.text.strip()
                    break
        if title:
            found["title"] = title

        description = meta.get("og:description") or meta.get("description")
        if not description:
            description = _longest_block(parsing.text_blocks(html))
        if description:
            found["description_text"] = description

        company = meta.get("og:site_name")
        if company:
            found["company_name"] = company
        return found

    def confidence(self, field_name: str) -> float:
        return FIELD_CONFIDENCE.get(field_name, 0.3)


def _longest_block(blocks: list[str]) -> str | None:
    """The longest block that is long enough to be a job description."""
    candidates = [block for block in blocks if len(block) >= MIN_DESCRIPTION_LENGTH]
    if not candidates:
        return None
    return max(candidates, key=len)
