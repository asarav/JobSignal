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
    # Title include/exclude (keywords or regex-safe).
    title = str(job.get("title") or "").lower()
    title_include = filters.get("title_include")
    if title_include:
        terms = title_include if isinstance(title_include, (list, tuple, set)) else [title_include]
        if not any(str(t).lower() in title for t in terms):
            return False
    title_exclude = filters.get("title_exclude")
    if title_exclude:
        terms = title_exclude if isinstance(title_exclude, (list, tuple, set)) else [title_exclude]
        if any(str(t).lower() in title for t in terms):
            return False

    allowed_workplace = filters.get("workplace_types")
    if allowed_workplace and not _equals_any(job.get("workplace_type"), allowed_workplace):
        return False

    allowed_employment = filters.get("employment_types")
    if allowed_employment and not _equals_any(job.get("employment_type"), allowed_employment):
        return False

    allowed_seniority = filters.get("seniorities")
    if allowed_seniority and not _equals_any(job.get("seniority"), allowed_seniority):
        return False

    # Salary floor/ceiling with missing policy.
    min_salary = filters.get("min_salary")
    if min_salary is not None:
        min_salary_int = _to_int(min_salary)
        if min_salary_int is not None:
            salary = _salary_min(job)
            if salary is None:
                policy = filters.get("treat_missing_salary_as", "pass")
                if policy == "fail":
                    return False
            elif salary < min_salary_int:
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
        # A job can list several places; match against all of them, not just
        # the first, so a country/city in a secondary location still counts.
        locations = _location_text(job.get("locations"))
        locations = locations or _location_text(job.get("location"))
        if location not in locations.lower():
            return False

    # Location countries.
    allowed_countries = filters.get("location_countries")
    if allowed_countries:
        countries = job.get("location_countries")
        if isinstance(countries, (list, tuple, set)):
            if not (set(str(c).lower() for c in countries) & set(str(c).lower() for c in (allowed_countries if isinstance(allowed_countries, (list, tuple, set)) else [allowed_countries]))):
                return False

    # Remote-eligible countries.
    remote_countries = filters.get("remote_eligible_countries")
    if remote_countries and str(job.get("workplace_type", "")).lower() == "remote":
        jc = job.get("location_countries")
        rset = set(str(c).lower() for c in (remote_countries if isinstance(remote_countries, (list, tuple, set)) else [remote_countries]))
        if isinstance(jc, (list, tuple, set)):
            if not (set(str(c).lower() for c in jc) & rset):
                return False

    # Newly collected after a given date (the first-seen timestamp).
    first_seen_after = filters.get("first_seen_after")
    if first_seen_after:
        first_seen = job.get("first_seen_at")
        if first_seen is None or str(first_seen) <= str(first_seen_after):
            return False

    # Posted after a given date.
    posted_after = filters.get("posted_after")
    if posted_after:
        posted = job.get("posted_at")
        if posted is None or str(posted) < str(posted_after):
            return False

    # Posting age max.
    posting_age_max = filters.get("posting_age_max_days")
    if posting_age_max is not None:
        import datetime as _dt

        age_days = None
        for field in ("posted_at", "first_seen_at"):
            val = job.get(field)
            if val:
                try:
                    t = _dt.datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                    age_days = (_dt.datetime.now(_dt.UTC) - t).days
                    break
                except ValueError:
                    continue
        if age_days is not None and age_days > int(str(posting_age_max)):
            return False

    # Source / company restriction.
    source_id = filters.get("source_id")
    if source_id and job.get("source_id") != source_id:
        return False

    company_id = filters.get("company_id")
    if company_id and job.get("company_id") != company_id:
        return False

    excluded_companies = filters.get("excluded_companies")
    if excluded_companies:
        ec = excluded_companies if isinstance(excluded_companies, (list, tuple, set)) else [excluded_companies]
        if job.get("company_id") in ec or job.get("company_name") in ec:
            return False

    # Only jobs with a salary.
    if filters.get("has_salary") is True:
        if _salary_min(job) is None and _salary_max(job) is None:
            return False

    # Required keywords.
    required_keywords = filters.get("required_keywords")
    if required_keywords:
        rks = required_keywords if isinstance(required_keywords, (list, tuple, set)) else [required_keywords]
        text = " ".join(str(job.get(k) or "") for k in ("title", "description_text")).lower()
        if not all(str(k).lower() in text for k in rks):
            return False

    # Excluded keywords.
    excluded_keywords = filters.get("excluded_keywords")
    if excluded_keywords:
        eks = excluded_keywords if isinstance(excluded_keywords, (list, tuple, set)) else [excluded_keywords]
        text = " ".join(str(job.get(k) or "") for k in ("title", "description_text")).lower()
        if any(str(k).lower() in text for k in eks):
            return False

    # Industries.
    industries = filters.get("industries")
    if industries:
        job_ind = job.get("industry")
        ins = industries if isinstance(industries, (list, tuple, set)) else [industries]
        if job_ind is not None and job_ind not in ins:
            return False

    # Work authorization.
    work_auth = filters.get("work_authorization")
    if work_auth:
        job_wa = job.get("work_authorization")
        was = work_auth if isinstance(work_auth, (list, tuple, set)) else [work_auth]
        if job_wa is not None and job_wa not in was:
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


def _location_text(value: object) -> str:
    """Flatten a location value of any shape into searchable text."""
    if isinstance(value, Mapping):
        return " ".join(str(item) for item in value.values() if item is not None)
    if isinstance(value, (list, tuple, set)):
        return " ".join(_location_text(item) for item in value)
    return "" if value is None else str(value)


def _salary_min(job: Mapping[str, object]) -> int | None:
    salary = job.get("salary")
    if isinstance(salary, Mapping):
        value = salary.get("min_amount")
        if value is None:
            value = salary.get("minValue")
        if value is None:
            value = salary.get("min")
        return _to_int(value)
    return None


def _salary_max(job: Mapping[str, object]) -> int | None:
    salary = job.get("salary")
    if isinstance(salary, Mapping):
        value = salary.get("max_amount")
        if value is None:
            value = salary.get("maxValue")
        if value is None:
            value = salary.get("max")
        return _to_int(value)
    return None
