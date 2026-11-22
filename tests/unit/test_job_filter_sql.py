"""SQLite filter compilation must match the Python hard-filter rules exactly."""

from __future__ import annotations

import json
import sqlite3

import pytest
from app.domain.services.hard_filters import apply_hard_filters
from app.infrastructure.persistence.sqlite.job_filters import compile_hard_filters

JOBS = [
    {
        "id": "j1",
        "title": "Software Engineer",
        "description_text": "Python and AWS platform work.",
        "workplace_type": "remote",
        "employment_type": "full-time",
        "seniority": "mid",
        "salary": {"min_amount": 100000, "max_amount": 150000},
        "locations": [
            {
                "raw": "New York, NY",
                "city": "New York",
                "region": "NY",
                "country": "United States",
            }
        ],
        "posted_at": "2026-01-15",
        "first_seen_at": "2026-01-16T00:00:00Z",
        "source_id": "src1",
        "company_id": "c1",
    },
    {
        "id": "j2",
        "title": "Product Manager",
        "description_text": "Roadmap and customer discovery.",
        "workplace_type": "onsite",
        "employment_type": "contract",
        "seniority": "senior",
        "salary": {"min_amount": 130000, "max_amount": 180000},
        "locations": [
            {
                "raw": "San Francisco, CA",
                "city": "San Francisco",
                "region": "CA",
                "country": "United States",
            }
        ],
        "posted_at": "2026-02-01",
        "first_seen_at": "2026-02-01T00:00:00Z",
        "source_id": "src2",
        "company_id": "c2",
    },
    {
        "id": "j3",
        "title": "Data Analyst",
        "description_text": "SQL reporting and dashboards.",
        "workplace_type": "hybrid",
        "employment_type": "part-time",
        "seniority": "entry",
        "salary": {"min_amount": 70000, "max_amount": 90000},
        "locations": [
            {
                "raw": "Austin, TX",
                "city": "Austin",
                "region": "TX",
                "country": "United States",
            }
        ],
        "posted_at": "2026-03-01",
        "first_seen_at": "2026-03-01T00:00:00Z",
        "source_id": "src1",
        "company_id": "c1",
    },
    {
        "id": "j4",
        "title": "DevOps Engineer",
        "description_text": "AWS infrastructure and reliability.",
        "workplace_type": "remote",
        "employment_type": "full-time",
        "seniority": "mid",
        "salary": {"min_amount": 90000, "max_amount": 120000},
        "locations": [
            {"raw": "Berlin", "country": "Germany", "lat": 52.52, "lon": 13.405}
        ],
        "posted_at": None,
        "first_seen_at": "2026-01-20T00:00:00Z",
        "source_id": "src3",
        "company_id": "c3",
    },
    {
        "id": "j5",
        "title": "Technical Writer",
        "description_text": "Writes product documentation.",
        "workplace_type": "remote",
        "employment_type": "full-time",
        "seniority": "mid",
        "salary": None,
        "locations": [],
        "posted_at": "2026-02-15",
        "first_seen_at": "2026-02-15T00:00:00Z",
        "source_id": "src4",
        "company_id": "c4",
    },
]

FILTER_CASES = [
    {},
    {"workplace_types": ["remote"]},
    {"workplace_types": ["remote", "onsite"]},
    {"employment_types": ["part-time"]},
    {"seniorities": ["mid"]},
    {"min_salary": 120000},
    {"min_salary": 120000, "treat_missing_salary_as": "fail"},
    {"max_salary": 100000},
    {"min_salary": 90000, "max_salary": 160000},
    {"has_salary": True},
    {"location": "York"},
    {"location": "austin"},
    {"location": "germany"},
    {"location": "52.52"},
    {"posted_after": "2026-02-01"},
    {"source_id": "src1"},
    {"company_id": "c1"},
    {"excluded_companies": ["c1"]},
    {"title_include": "engineer"},
    {"title_include": ["analyst", "writer"]},
    {"title_exclude": ["engineer"]},
    {"title_exclude": ["engineer", "manager"]},
    {"required_keywords": ["python", "aws"]},
    {"excluded_keywords": ["python"]},
    {
        "workplace_types": ["remote"],
        "title_include": "engineer",
        "location": "germany",
    },
]


def _database() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE jobs("
        "id TEXT, title TEXT, description_text TEXT, locations_json TEXT, "
        "workplace_type TEXT, employment_type TEXT, seniority TEXT, "
        "salary_json TEXT, posted_at TEXT, first_seen_at TEXT, "
        "source_id TEXT, company_id TEXT)"
    )
    for job in JOBS:
        con.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                job["id"],
                job["title"],
                job["description_text"],
                json.dumps(job["locations"]),
                job["workplace_type"],
                job["employment_type"],
                job["seniority"],
                json.dumps(job["salary"]) if job["salary"] else None,
                job["posted_at"],
                job["first_seen_at"],
                job["source_id"],
                job["company_id"],
            ),
        )
    return con


@pytest.mark.parametrize("filters", FILTER_CASES)
def test_compiled_filters_match_python(filters):
    compiled = compile_hard_filters(filters)
    assert compiled is not None
    conditions, params = compiled
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    with _database() as con:
        sql_ids = {row[0] for row in con.execute(f"SELECT id FROM jobs{where}", params)}
    python_ids = {job["id"] for job in apply_hard_filters(JOBS, filters)}
    assert sql_ids == python_ids


def test_python_only_filter_falls_back():
    assert compile_hard_filters({"posting_age_max_days": 30}) is None
    assert compile_hard_filters({"location_countries": ["US"]}) is None
