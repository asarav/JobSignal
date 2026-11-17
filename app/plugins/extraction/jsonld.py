"""``jsonld``: schema.org JobPosting markup (M2).

``<script type="application/ld+json">`` blocks are the most reliable markup
a careers page can offer, so this strategy runs right after the structured
JSON endpoint.
"""

from __future__ import annotations

import json
from typing import Any

from app.domain.models.extraction import ExtractionTarget
from app.infrastructure.html import parsing
from app.plugins.extraction.base import StrategyBase

JOB_POSTING_TYPES = ("JobPosting", "JobPostingList")

FIELD_CONFIDENCE = {
    "title": 0.95,
    "description_text": 0.9,
    "locations": 0.8,
    "company_name": 0.8,
    "employment_type": 0.8,
    "posted_at": 0.75,
    "salary": 0.7,
}


class JsonLdStrategy(StrategyBase):
    id = "extract.jsonld"
    default_order = 20

    def can_attempt(self, target: ExtractionTarget) -> bool:
        return bool(target.html) and "application/ld+json" in (target.html or "")

    def fields(self, target: ExtractionTarget) -> dict[str, Any]:
        for block in parsing.scripts(target.html or ""):
            if block.attrs.get("type", "").lower() != "application/ld+json":
                continue
            payload = _load(block.text)
            for posting in _iter_postings(payload):
                fields = _posting_fields(posting)
                if fields:
                    return fields
        return {}

    def confidence(self, field_name: str) -> float:
        return FIELD_CONFIDENCE.get(field_name, 0.8)


def _load(text: str) -> Any:
    try:
        return json.loads(text.strip() or "{}")
    except ValueError:
        return None


def _iter_postings(payload: Any):
    """Yield every JobPosting object in a JSON-LD document."""
    if isinstance(payload, list):
        for item in payload:
            yield from _iter_postings(item)
        return
    if not isinstance(payload, dict):
        return
    graph = payload.get("@graph")
    if isinstance(graph, list):
        for item in graph:
            yield from _iter_postings(item)
    types = payload.get("@type")
    types = [types] if isinstance(types, str) else (types or [])
    if any(str(t) in JOB_POSTING_TYPES for t in types):
        yield payload
    for key in ("jobPosting", "mainEntity", "itemListElement"):
        nested = payload.get(key)
        if isinstance(nested, (dict, list)):
            yield from _iter_postings(nested)


def _posting_fields(posting: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = {}
    title = posting.get("title") or posting.get("name")
    if isinstance(title, str) and title.strip():
        found["title"] = title.strip()
    description = posting.get("description")
    if isinstance(description, str) and description.strip():
        found["description_text"] = description.strip()
    locations = _locations(posting.get("jobLocation"))
    if locations:
        found["locations"] = locations
    organization = posting.get("hiringOrganization")
    if isinstance(organization, dict) and organization.get("name"):
        found["company_name"] = organization["name"]
    employment = posting.get("employmentType")
    if isinstance(employment, str) and employment.strip():
        found["employment_type"] = employment.strip()
    posted = posting.get("datePosted")
    if isinstance(posted, str) and posted.strip():
        found["posted_at"] = posted.strip()
    salary = posting.get("baseSalary")
    if salary:
        found["salary"] = salary
    return found


def _locations(value: Any) -> list[str]:
    """Flatten schema.org jobLocation into a list of readable locations."""
    if value is None:
        return []
    entries = value if isinstance(value, list) else [value]
    locations: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        address = entry.get("address")
        if isinstance(address, dict):
            parts = [
                str(address.get(key))
                for key in ("addressLocality", "addressRegion", "addressCountry")
                if address.get(key)
            ]
            if parts:
                locations.append(", ".join(parts))
                continue
        if entry.get("name"):
            locations.append(str(entry["name"]))
    return locations
