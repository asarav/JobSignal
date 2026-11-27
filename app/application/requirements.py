"""Lazy, bounded requirement analysis for postings already in the database.

Normalization runs the analyzer for new postings.  Rows written before the
requirements column existed (or before an analyzer version bump) are corrected
when a matching/ranking call touches them, without a manual backfill button.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.services.job_requirements import (
    REQUIREMENTS_VERSION,
    analysis_hash_for,
    analyze_job,
)


def location_text(row: Mapping[str, Any]) -> str:
    locations = row.get("locations") or []
    if not isinstance(locations, (list, tuple)):
        return ""
    values: list[str] = []
    for item in locations:
        if isinstance(item, Mapping):
            value = item.get("raw") or item.get("city")
        else:
            value = item
        if value:
            values.append(str(value))
    return ", ".join(values)


async def ensure_requirements(
    uow: Any, row: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a job dict with current analyzer output, persisting it if stale."""
    job = dict(row)
    title = str(job.get("title") or "")
    description = str(job.get("description_text") or "")
    location = location_text(job)
    workplace = str(job.get("workplace_type") or "unknown")
    expected = analysis_hash_for(title, description, location, workplace)
    existing = job.get("requirements")
    requirements = dict(existing) if isinstance(existing, Mapping) else {}
    if (
        requirements.get("version") == REQUIREMENTS_VERSION
        and requirements.get("analysis_hash") == expected
    ):
        job["requirements"] = requirements
        return job

    analysis = analyze_job(
        title,
        description,
        location_text=location,
        workplace_type=workplace,
    )
    payload = analysis.to_dict()
    await uow.jobs.update_requirements(
        str(job.get("id")),
        payload,
        analysis.analysis_hash,
        required_skills=analysis.required_skills,
        preferred_skills=analysis.preferred_skills,
    )
    job["requirements"] = payload
    job["requirements_hash"] = analysis.analysis_hash
    job["required_skills"] = list(analysis.required_skills)
    job["preferred_skills"] = list(analysis.preferred_skills)
    return job


__all__ = ["ensure_requirements", "location_text"]
