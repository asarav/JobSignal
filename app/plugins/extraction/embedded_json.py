"""``embedded_json``: job objects embedded in the page (M2).

Modern single-page apps ship their data as a JSON blob in a ``<script>``
tag. This strategy looks for the usual containers (``__NEXT_DATA__``,
``__INITIAL_STATE__``, ``window.__INITIAL_STATE__ = {...}``) and then searches
the decoded object tree for something that looks like a job.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.domain.models.extraction import ExtractionTarget
from app.infrastructure.html import parsing
from app.plugins.extraction.base import StrategyBase

# Container ids/attributes used by common frameworks.
CONTAINER_IDS = ("__NEXT_DATA__", "__INITIAL_STATE__", "__APOLLO_STATE__", "__NUXT__")
ASSIGNMENT_RE = re.compile(
    r"window\.(?:__INITIAL_STATE__|__PRELOADED_STATE__|__APOLLO_STATE__)\s*=\s*(\{.*?\})\s*;",
    re.DOTALL,
)

# A dictionary is treated as a job when it has a title and one of these keys.
JOB_MARKERS = (
    "description",
    "content",
    "jobDescription",
    "requirements",
    "location",
    "locations",
    "salary",
    "postedAt",
    "publishedAt",
)

TITLE_KEYS = ("title", "name", "jobTitle", "text", "position")
DESCRIPTION_KEYS = ("description", "content", "jobDescription", "descriptionHtml", "body")
LOCATION_KEYS = ("location", "locations", "locationName", "office", "workplace")
EMPLOYMENT_KEYS = ("employmentType", "employment_type", "jobType", "type")
POSTED_KEYS = ("postedAt", "publishedAt", "createdAt", "updatedAt", "datePosted")
COMPANY_KEYS = ("companyName", "company", "organization")

FIELD_CONFIDENCE = {
    "title": 0.8,
    "description_text": 0.75,
    "locations": 0.6,
    "company_name": 0.6,
    "employment_type": 0.6,
    "posted_at": 0.55,
}


class EmbeddedJsonStrategy(StrategyBase):
    id = "extract.embedded_json"
    default_order = 30

    def can_attempt(self, target: ExtractionTarget) -> bool:
        if not target.html:
            return False
        html = target.html
        return any(marker in html for marker in CONTAINER_IDS) or "window.__INITIAL_STATE__" in html

    def fields(self, target: ExtractionTarget) -> dict[str, Any]:
        for payload in self._payloads(target.html or ""):
            job = _find_job(payload)
            if job is not None:
                fields = _job_fields(job)
                if fields:
                    return fields
        return {}

    def _payloads(self, html: str) -> list[Any]:
        payloads: list[Any] = []
        for block in parsing.scripts(html):
            identifier = block.attrs.get("id", "")
            if identifier in CONTAINER_IDS:
                decoded = _load(block.text)
                if decoded is not None:
                    payloads.append(decoded)
            for match in ASSIGNMENT_RE.finditer(block.text):
                decoded = _load(match.group(1))
                if decoded is not None:
                    payloads.append(decoded)
        return payloads

    def confidence(self, field_name: str) -> float:
        return FIELD_CONFIDENCE.get(field_name, 0.6)


def _load(text: str) -> Any:
    try:
        return json.loads(text.strip() or "null")
    except ValueError:
        return None


def _find_job(payload: Any, depth: int = 0) -> dict[str, Any] | None:
    """Breadth-first search for the first dictionary that looks like a job."""
    if depth > 12:
        return None
    if isinstance(payload, list):
        for item in payload:
            found = _find_job(item, depth + 1)
            if found is not None:
                return found
        return None
    if not isinstance(payload, dict):
        return None
    if _looks_like_job(payload):
        return payload
    for value in payload.values():
        found = _find_job(value, depth + 1)
        if found is not None:
            return found
    return None


def _looks_like_job(payload: dict[str, Any]) -> bool:
    if not any(isinstance(payload.get(key), str) and payload.get(key) for key in TITLE_KEYS):
        return False
    return any(key in payload for key in JOB_MARKERS)


def _job_fields(job: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for name, keys in (
        ("title", TITLE_KEYS),
        ("description_text", DESCRIPTION_KEYS),
        ("locations", LOCATION_KEYS),
        ("employment_type", EMPLOYMENT_KEYS),
        ("posted_at", POSTED_KEYS),
        ("company_name", COMPANY_KEYS),
    ):
        value = _first(job, keys)
        if value not in (None, "", [], {}):
            found[name] = value
    return found


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            return value
    return None
