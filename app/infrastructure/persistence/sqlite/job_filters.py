"""Compile hard filters into SQLite predicates.

The pure rules live in :mod:`app.domain.services.hard_filters`.  SQLite gets an
equivalent fast path here so a filtered page can be counted and paginated by the
database instead of materializing the whole job table.

Only filters that can be expressed exactly against stored columns are compiled.
Filters that need derived data the row does not contain return ``None``; the
repository then falls back to the exhaustive Python scan, so results stay
correct even as new filter types are added.
"""

from __future__ import annotations

from collections.abc import Mapping

#: Filters whose inputs are not stored on a job row (yet).  They must keep using
#: the Python implementation rather than being silently ignored by SQL.
_PYTHON_ONLY = frozenset(
    {
        "posting_age_max_days",
        "industries",
        "industry",
        "work_authorization",
        "location_countries",
        "remote_eligible_countries",
    }
)

#: Location JSON stores LocationRef objects; these are the values the domain
#: filter flattens.  Keep this expression in sync with ``_location_text``.
_LOCATION_TEXT = (
    "COALESCE(json_extract(loc.value, '$.raw'), '') || ' ' || "
    "COALESCE(json_extract(loc.value, '$.city'), '') || ' ' || "
    "COALESCE(json_extract(loc.value, '$.region'), '') || ' ' || "
    "COALESCE(json_extract(loc.value, '$.country'), '') || ' ' || "
    "COALESCE(json_extract(loc.value, '$.lat'), '') || ' ' || "
    "COALESCE(json_extract(loc.value, '$.lon'), '') || ' ' || "
    "CASE WHEN json_type(loc.value) IN ('text', 'integer', 'real') "
    "THEN CAST(loc.value AS TEXT) ELSE '' END"
)

_SALARY_MIN = (
    "COALESCE(json_extract(jobs.salary_json, '$.min_amount'), "
    "json_extract(jobs.salary_json, '$.minValue'), "
    "json_extract(jobs.salary_json, '$.min'))"
)
_SALARY_MAX = (
    "COALESCE(json_extract(jobs.salary_json, '$.max_amount'), "
    "json_extract(jobs.salary_json, '$.maxValue'), "
    "json_extract(jobs.salary_json, '$.max'))"
)
_TEXT = "lower(jobs.title || ' ' || jobs.description_text)"


def compile_hard_filters(
    filters: Mapping[str, object],
) -> tuple[list[str], list[object]] | None:
    """Return ``(conditions, params)`` for ``filters``, or None to fall back.

    Conditions are ANDed together by the caller.  The result is a superset of
    nothing: it is intended to match :func:`apply_hard_filters` exactly.
    """
    if any(filters.get(key) is not None for key in _PYTHON_ONLY):
        return None

    conditions: list[str] = []
    params: list[object] = []

    title_include = _terms(filters.get("title_include"))
    if title_include:
        parts = ["instr(lower(jobs.title), lower(?)) > 0" for _ in title_include]
        conditions.append("(" + " OR ".join(parts) + ")")
        params.extend(title_include)

    title_exclude = _terms(filters.get("title_exclude"))
    if title_exclude:
        parts = ["instr(lower(jobs.title), lower(?)) = 0" for _ in title_exclude]
        conditions.append("(" + " AND ".join(parts) + ")")
        params.extend(title_exclude)

    for key, column in (
        ("workplace_types", "workplace_type"),
        ("employment_types", "employment_type"),
        ("seniorities", "seniority"),
    ):
        values = _terms(filters.get(key))
        if values:
            placeholders = ", ".join("?" for _ in values)
            conditions.append(f"jobs.{column} IN ({placeholders})")
            params.extend(values)

    min_salary = _to_int(filters.get("min_salary"))
    if min_salary is not None:
        if filters.get("treat_missing_salary_as") == "fail":
            conditions.append(f"({_SALARY_MIN} IS NOT NULL AND {_SALARY_MIN} >= ?)")
        else:
            conditions.append(f"({_SALARY_MIN} IS NULL OR {_SALARY_MIN} >= ?)")
        params.append(min_salary)

    max_salary = _to_int(filters.get("max_salary"))
    if max_salary is not None:
        conditions.append(f"({_SALARY_MAX} IS NULL OR {_SALARY_MAX} <= ?)")
        params.append(max_salary)

    if filters.get("has_salary") is True:
        conditions.append(f"({_SALARY_MIN} IS NOT NULL OR {_SALARY_MAX} IS NOT NULL)")

    location = filters.get("location")
    if location:
        conditions.append(
            "json_valid(jobs.locations_json) AND EXISTS ("
            "SELECT 1 FROM json_each(jobs.locations_json) AS loc "
            f"WHERE instr(lower({_LOCATION_TEXT}), lower(?)) > 0)"
        )
        params.append(str(location))

    required_keywords = _terms(filters.get("required_keywords"))
    for term in required_keywords:
        conditions.append(f"instr({_TEXT}, lower(?)) > 0")
        params.append(term)

    excluded_keywords = _terms(filters.get("excluded_keywords"))
    for term in excluded_keywords:
        conditions.append(f"instr({_TEXT}, lower(?)) = 0")
        params.append(term)

    first_seen_after = filters.get("first_seen_after")
    if first_seen_after:
        conditions.append(
            "(jobs.first_seen_at IS NOT NULL AND jobs.first_seen_at > ?)"
        )
        params.append(str(first_seen_after))

    posted_after = filters.get("posted_after")
    if posted_after:
        conditions.append("(jobs.posted_at IS NOT NULL AND jobs.posted_at >= ?)")
        params.append(str(posted_after))

    source_id = filters.get("source_id")
    if source_id:
        conditions.append("jobs.source_id = ?")
        params.append(source_id)

    company_id = filters.get("company_id")
    if company_id:
        conditions.append("jobs.company_id = ?")
        params.append(company_id)

    excluded_companies = _terms(filters.get("excluded_companies"))
    if excluded_companies:
        placeholders = ", ".join("?" for _ in excluded_companies)
        conditions.append(f"jobs.company_id NOT IN ({placeholders})")
        params.extend(excluded_companies)

    return conditions, params


def _terms(value: object) -> list[object]:
    """Normalize a scalar or sequence filter value to a list of terms."""
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value] if value is not None else []


def _to_int(value: object) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None
