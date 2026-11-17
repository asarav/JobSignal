"""Hard filters for job search (M3).

A hard filter removes a job from the result set when it does not satisfy a
constraint. Filters are composable: every filter in the profile must pass.

Filters are expressed as a plain mapping so they can be stored in a search
profile and edited from the UI without a schema change.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def apply_hard_filters(
    jobs: Sequence[Mapping[str, object]], filters: Mapping[str, object]
) -> list[Mapping[str, object]]:
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
    if allowed_workplace and not _equals_any(job.get("workplace_type"), allowed_workplace):
        return False

    allowed_employment = filters.get("employment_types")
    if allowed_employment and not _equals_any(job.get("employment_type"), allowed_employment):
        return False

    allowed_seniority = filters.get("seniorities")
    if allowed_seniority and not _equals_any(job.get("seniority"), allowed_seniority):
        return False

    # Salary floor/ceiling.
    min_salary = filters.get("min_salary")
    if min_salary is not None:
        min_salary_int = _to_int(min_salary)
        if min_salary_int is not None:
            salary = _salary_min(job)
            if salary is not None and salary < min_salary_int:
                return False

    max_salary = filters.get("max_salary")
    if max_salary is not None:
        max_salary_int = _to_int(max_salary)
        if max_salary_int is not None:
            salary = _salary_max(job)
            if salary is not None and salary > max_salary_int:
                return False

    # Location substring (case-insensitive).
    location = filters.get("location")
    if location:
        location = str(location).lower()
        locations = _get_str_or_list(job.get("locations"))
        locations = locations or _get_str_or_list(job.get("location")) or ""
        if location not in locations.lower():
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


def _equals_any(value: object, allowed: object) -> bool:
    """Return True if ``value`` equals any item in ``allowed``.

    Both values may be a list/tuple/set of allowed values, or a single value.
    """
    if isinstance(allowed, (list, tuple, set)):
        return any(_equals_any(value, item) for item in allowed)
    return value == allowed


def _to_int(value: object) -> int | None:
    """Convert a value to int, returning None if not convertible."""
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _get_str_or_list(value: object) -> str | None:
    """Return a string if the value is a string, otherwise return the
    string representation of the first element of a list/set."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > 0:
            first = value[0]
            if isinstance(first, str):
                return first
            if isinstance(first, Mapping):
                return str(first)
        return str(value) if len(value) > 0 else None
    if isinstance(value, set):
        items = list(value)
        if items:
            first = items[0]
            if isinstance(first, str):
                return first
            if isinstance(first, Mapping):
                return str(first)
        return str(value) if len(items) > 0 else None
    return str(value) if value is not None else None


def _salary_min(job: Mapping[str, object]) -> int | None:
    salary = job.get("salary")
    if isinstance(salary, Mapping):
        value = salary.get("min_amount") or salary.get("minValue") or salary.get("min")
        return _to_int(value)
    return None


def _salary_max(job: Mapping[str, object]) -> int | None:
    salary = job.get("salary")
    if isinstance(salary, Mapping):
        value = salary.get("max_amount") or salary.get("maxValue") or salary.get("max")
        return _to_int(value)
    return None
