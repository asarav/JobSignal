"""``public_endpoint``: fields from a structured JSON payload (M2).

Many ATSs expose a public JSON API for their job board. When the crawler
already holds that payload, no HTML parsing is needed and the data is
trustworthy, so this strategy runs first.
"""

from __future__ import annotations

from typing import Any

from app.domain.models.extraction import ExtractionTarget
from app.plugins.extraction.base import StrategyBase

# Keys we accept for each field, most specific first.
TITLE_KEYS = ("title", "name", "jobTitle", "text")
DESCRIPTION_KEYS = ("content", "description", "description_plain", "body", "jobDescription")
LOCATION_KEYS = ("location", "locations", "location_name", "office")
COMPANY_KEYS = ("companyName", "company", "organization", "hiringOrganization")
EMPLOYMENT_KEYS = ("employmentType", "employment_type", "jobType")
POSTED_KEYS = ("updated_at", "updatedAt", "posted_at", "postedAt", "createdAt", "created_at")
SALARY_KEYS = ("salary", "salary_range", "payRange")

# Confidence per field: structured data is reliable but not perfect.
FIELD_CONFIDENCE = {
    "title": 0.9,
    "description_text": 0.85,
    "locations": 0.7,
    "company_name": 0.7,
    "employment_type": 0.7,
    "posted_at": 0.6,
    "salary": 0.6,
}


class PublicEndpointStrategy(StrategyBase):
    id = "extract.public_endpoint"
    default_order = 10

    def can_attempt(self, target: ExtractionTarget) -> bool:
        return bool(target.json_payload)

    def fields(self, target: ExtractionTarget) -> dict[str, Any]:
        payload = target.json_payload or {}
        # Some endpoints wrap the job in an envelope (``{"job": {...}}``).
        job = payload
        for key in ("job", "posting", "result", "data"):
            nested = payload.get(key)
            if isinstance(nested, dict):
                job = nested
                break

        found: dict[str, Any] = {}
        title = _first(job, TITLE_KEYS)
        if title:
            found["title"] = title
        description = _first(job, DESCRIPTION_KEYS)
        if description:
            found["description_text"] = description
        locations = _first(job, LOCATION_KEYS)
        if locations:
            found["locations"] = locations
        company = _first(job, COMPANY_KEYS)
        if company:
            found["company_name"] = company
        employment = _first(job, EMPLOYMENT_KEYS)
        if employment:
            found["employment_type"] = employment
        posted = _first(job, POSTED_KEYS)
        if posted:
            found["posted_at"] = posted
        salary = _first(job, SALARY_KEYS)
        if salary:
            found["salary"] = salary
        return found

    def confidence(self, field_name: str) -> float:
        return FIELD_CONFIDENCE.get(field_name, 0.6)


def _first(payload: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, "", [], {}):
            return value
    return None
