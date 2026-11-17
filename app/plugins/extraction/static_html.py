"""``static_html``: fields from well-known ATS markup (M2).

Career pages built on common ATS templates label their sections with stable
class names (``job-description``, ``job-location``, ``employment-type`` ...).
This strategy reads those; it never executes JavaScript.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.models.extraction import ExtractionTarget
from app.infrastructure.html import parsing
from app.plugins.extraction.base import StrategyBase

# Class names that mark a section, most specific first.
DESCRIPTION_MARKERS = (
    "job-description",
    "jobdescription",
    "job-content",
    "jobcontent",
    "posting-content",
    "postingcontent",
    "description__text",
    "content-description",
    "vacancy-description",
)
LOCATION_MARKERS = ("job-location", "joblocation", "location__text", "posting-location")
EMPLOYMENT_MARKERS = ("employment-type", "employmenttype", "job-type")
SALARY_MARKERS = ("job-salary", "jobsalary", "salary-range", "compensation")

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")

FIELD_CONFIDENCE = {
    "title": 0.7,
    "description_text": 0.6,
    "locations": 0.55,
    "employment_type": 0.55,
    "salary": 0.5,
}


class StaticHtmlStrategy(StrategyBase):
    id = "extract.static_html"
    default_order = 40

    def can_attempt(self, target: ExtractionTarget) -> bool:
        return bool(target.html)

    def fields(self, target: ExtractionTarget) -> dict[str, Any]:
        html = target.html or ""
        found: dict[str, Any] = {}

        heading = _first_heading(html)
        if heading:
            found["title"] = heading
        description = _section_text(html, DESCRIPTION_MARKERS)
        if description:
            found["description_text"] = description
        location = _section_text(html, LOCATION_MARKERS)
        if location:
            found["locations"] = [location]
        employment = _section_text(html, EMPLOYMENT_MARKERS)
        if employment:
            found["employment_type"] = employment
        salary = _section_text(html, SALARY_MARKERS)
        if salary:
            found["salary"] = salary
        return found

    def confidence(self, field_name: str) -> float:
        return FIELD_CONFIDENCE.get(field_name, 0.5)


def _first_heading(html: str) -> str | None:
    for heading in parsing.headings(html):
        if heading.level == 1 and heading.text.strip():
            return heading.text.strip()
    return None


def _section_text(html: str, markers: tuple[str, ...]) -> str | None:
    """Text of the first element whose class attribute contains a marker."""
    for marker in markers:
        for match in re.finditer(
            rf'<(?P<tag>[a-zA-Z][\w-]*)[^>]*class=["\'][^"\']*{re.escape(marker)}[^"\']*["\'][^>]*>',
            html,
        ):
            start = match.end()
            tag = match.group("tag")
            end = html.find(f"</{tag}>", start)
            chunk = html[start : end if end != -1 else start + 4000]
            text = SPACE_RE.sub(" ", TAG_RE.sub(" ", chunk)).strip()
            if text:
                return text
    return None
