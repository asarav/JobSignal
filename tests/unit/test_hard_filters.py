"""Unit tests for M3 hard filters."""

from __future__ import annotations

from app.domain.services.hard_filters import apply_hard_filters

JOBS = [
    {
        "id": "j1",
        "title": "Software Engineer",
        "workplace_type": "remote",
        "employment_type": "full-time",
        "seniority": "mid",
        "salary": {"min_amount": 100000, "max_amount": 150000},
        "locations": ["New York, NY"],
        "posted_at": "2026-01-15",
        "source_id": "src1",
        "company_id": "c1",
    },
    {
        "id": "j2",
        "title": "Product Manager",
        "workplace_type": "onsite",
        "employment_type": "contract",
        "seniority": "senior",
        "salary": {"min_amount": 130000, "max_amount": 180000},
        "locations": ["San Francisco, CA"],
        "posted_at": "2026-02-01",
        "source_id": "src2",
        "company_id": "c2",
    },
    {
        "id": "j3",
        "title": "Data Analyst",
        "workplace_type": "hybrid",
        "employment_type": "part-time",
        "seniority": "entry",
        "salary": {"min_amount": 70000, "max_amount": 90000},
        "locations": ["Austin, TX"],
        "posted_at": "2026-03-01",
        "source_id": "src1",
        "company_id": "c1",
    },
    {
        "id": "j4",
        "title": "DevOps Engineer",
        "workplace_type": "remote",
        "employment_type": "full-time",
        "seniority": "mid",
        "salary": {"min_amount": 90000, "max_amount": 120000},
        "locations": None,
        "posted_at": None,
        "source_id": "src3",
        "company_id": "c3",
    },
]


def test_no_filters_returns_all():
    result = apply_hard_filters(JOBS, {})
    assert len(result) == 4


def test_workplace_type_filter():
    result = apply_hard_filters(JOBS, {"workplace_types": ["remote"]})
    ids = {j["id"] for j in result}
    assert ids == {"j1", "j4"}


def test_workplace_type_filter_or_within_list():
    result = apply_hard_filters(JOBS, {"workplace_types": ["remote", "onsite"]})
    ids = {j["id"] for j in result}
    assert ids == {"j1", "j2", "j4"}


def test_employment_type_filter():
    result = apply_hard_filters(JOBS, {"employment_types": ["part-time"]})
    assert len(result) == 1
    assert result[0]["id"] == "j3"


def test_seniority_filter():
    result = apply_hard_filters(JOBS, {"seniorities": ["mid"]})
    ids = {j["id"] for j in result}
    assert ids == {"j1", "j4"}


def test_min_salary_filter():
    result = apply_hard_filters(JOBS, {"min_salary": 120000})
    ids = {j["id"] for j in result}
    assert ids == {"j2"}


def test_max_salary_filter():
    result = apply_hard_filters(JOBS, {"max_salary": 100000})
    ids = {j["id"] for j in result}
    assert ids == {"j3"}  # j4 max 120k exceeds 100k cap; j1/j2 exceed cap


def test_salary_range_filter():
    result = apply_hard_filters(JOBS, {"min_salary": 90000, "max_salary": 160000})
    ids = {j["id"] for j in result}
    assert ids == {"j1", "j4"}  # j2 max 180k exceeds 160k cap; j3 min 70k below 90k floor


def test_location_substring_filter():
    result = apply_hard_filters(JOBS, {"location": "York"})
    assert len(result) == 1
    assert result[0]["id"] == "j1"


def test_location_case_insensitive():
    result = apply_hard_filters(JOBS, {"location": "austin"})
    assert len(result) == 1
    assert result[0]["id"] == "j3"


def test_posted_after_filter():
    result = apply_hard_filters(JOBS, {"posted_after": "2026-02-01"})
    ids = {j["id"] for j in result}
    assert ids == {"j2", "j3"}


def test_posted_after_no_date_excluded():
    result = apply_hard_filters(JOBS, {"posted_after": "2026-01-01"})
    ids = {j["id"] for j in result}
    # j4 has no posted_at, so excluded
    assert ids == {"j1", "j2", "j3"}


def test_source_id_filter():
    result = apply_hard_filters(JOBS, {"source_id": "src1"})
    ids = {j["id"] for j in result}
    assert ids == {"j1", "j3"}


def test_company_id_filter():
    result = apply_hard_filters(JOBS, {"company_id": "c1"})
    ids = {j["id"] for j in result}
    assert ids == {"j1", "j3"}


def test_has_salary_true():
    result = apply_hard_filters(JOBS, {"has_salary": True})
    ids = {j["id"] for j in result}
    assert ids == {"j1", "j2", "j3", "j4"}


def test_all_filters_combined():
    result = apply_hard_filters(
        JOBS,
        {
            "workplace_types": ["remote"],
            "employment_types": ["full-time"],
            "seniorities": ["mid"],
            "min_salary": 90000,
            "max_salary": 200000,
            "source_id": "src1",
        },
    )
    assert len(result) == 1
    assert result[0]["id"] == "j1"


def test_empty_job_list():
    assert apply_hard_filters([], {"workplace_types": ["remote"]}) == []