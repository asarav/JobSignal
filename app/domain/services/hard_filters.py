"""Hard filters for job search (M3).

A hard filter removes a job from the result set when it does not satisfy a
constraint. Filters are composable: every filter in the profile must pass.

Filters are expressed as a plain mapping so they can be stored in a search
profile and edited from the UI without a schema change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime


def apply_hard_filters(jobs: Sequence[Mapping[str, object]], filters: Mapping[str, object]) -> list[Mapping[str, object]]:
    """Return the jobs that satisfy every filter in ``filters``."""
    if not filters:
        return list(jobs)
    result: list[Mapping[str, object]] = []
    for job in jobs:
        if _passes(job, filters):
            result.append(job)
    return result


def _passes(job: Mapping[str, object], filters: Mapping[str, object]) -> bool:
    # Workplace type: allowed values (OR within the list).
    allowed_workplace = filters.get("workplace_types")
    if allowed_workplace and job.get("workplace_type") not in allowed_workplace:
        return False

    allowed_employment = filters.get("employment_types")
    if allowed_employment and job.get("employment_type") not in allowed_employment:
        return False

    allowed_seniority = filters.get("seniorities")
    if allowed_seniority and job.get("seniority") not in allowed_seniority:
        return False

    # Salary floor/ceiling.
    min_salary = filters.get("min_salary")
    if min_salary is not None:
        salary = _salary_min(job)
        if salary is None or salary < int(min_salary):
            return False

    max_salary = filters.get("max_salary")
    if max_salary is not None:
        salary = _salary_max(job)
        if salary is not None and salary > int(max_salary):
            return False

    # Location substring (case-insensitive).
    location = filters.get("location")
    if location:
        haystack = str(job.get("locations") or job.get("location") or "").lower()
        if str(location).lower() not in haystack:
            return False

    # Posted after a given date.
    posted_after = filters.get("posted_after")
    if posted_after:
        posted = job.get("posted_at")
        if posted is None or str(posted) < str(posted_after):
            return False

    # Source / company restriction.
    source_id = filters.get("source_id")
    if source_id and job.get("source_id") != source_id:
        return False

    company_id = filters.get("company_id")
    if company_id and job.get("company_id") != company_id:
        return False

    # Only jobs with a salary.
    if filters.get("has_salary") is True:
        if _salary_min(job) is None and _salary_max(job) is None:
            return False

    return True


def _salary_min(job: Mapping[str, object]) -> int | None:
    salary = job.get("salary")
    if isinstance(salary, Mapping):
        value = salary.get("min_amount") or salary.get("minValue") or salary.get("min")
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def _salary_max(job: Mapping[str, object]) -> int | None:
    salary = job.get("salary")
    if isinstance(salary, Mapping):
        value = salary.get("max_amount") or salary.get("maxValue") or salary.get("max")
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None
