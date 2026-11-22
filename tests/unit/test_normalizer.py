"""Normalizer v1: only assert what the payload supports."""

from datetime import UTC, datetime

from app.domain.models.enums import EmploymentType, Seniority, WorkplaceType
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import SourceConfig
from app.domain.services.normalizer import (
    company_id_for,
    company_name_for_source,
    content_hash_for,
    infer_employment_type,
    infer_seniority,
    normalize_job,
    normalize_title,
    title_family,
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _raw(payload: dict, title: str = "Senior Backend Engineer") -> RawJob:
    return RawJob(
        ref=RawJobRef(
            source_id="s1",
            url="https://boards.greenhouse.io/acme/jobs/1",
            source_posting_id="1",
        ),
        title=title,
        body_text="Build things",
        body_html="<p>Build things</p>",
        payload=payload,
    )


def _source() -> SourceConfig:
    return SourceConfig(
        id="s1",
        url="https://boards.greenhouse.io/acme",
        kind="ats",
        connector_id="ats.greenhouse",
        ats_tenant_key="acme",
    )


def test_normalize_title_strips_punctuation():
    assert normalize_title("Senior Backend Engineer (Remote)") == "senior backend engineer remote"


def test_normalize_title_folds_accents_without_splitting_words():
    assert normalize_title("Analyste de systèmes cliniques") == "analyste de systemes cliniques"
    assert title_family(normalize_title("Ingénieur systèmes")) == "ingenieur systemes"


def test_normalize_title_keeps_non_latin_letters():
    assert normalize_title("Разработчик Python") == "разработчик python"


def test_title_family_ignores_seniority():
    assert title_family(normalize_title("Senior Backend Engineer")) == "backend engineer"
    assert title_family(normalize_title("Backend Engineer")) == "backend engineer"


def test_infer_seniority():
    assert infer_seniority("Staff Engineer") is Seniority.STAFF
    assert infer_seniority("Junior Analyst") is Seniority.ENTRY
    assert infer_seniority("Data Engineer") is Seniority.UNKNOWN


def test_infer_employment_type():
    assert infer_employment_type("Marketing Internship") is EmploymentType.INTERNSHIP
    assert infer_employment_type("Contract Counsel") is EmploymentType.CONTRACT
    assert infer_employment_type("Data Engineer") is EmploymentType.UNKNOWN


def test_company_name_prefers_tenant_key():
    assert company_name_for_source(_source()) == "acme"


def test_company_name_falls_back_to_path_segment():
    source = SourceConfig(id="s2", url="https://jobs.lever.co/northwind", kind="ats")
    assert company_name_for_source(source) == "northwind"


def test_company_id_is_stable_and_normalized():
    assert company_id_for("Acme") == company_id_for("acme")
    assert company_id_for("acme") != company_id_for("other")


def test_normalize_job_extracts_supported_fields():
    payload = {
        "location": "Berlin, Germany",
        "employmentType": "full_time",
        "workplaceType": "hybrid",
        "salary": {"minValue": 50000, "maxValue": 70000, "currency": "EUR", "period": "YEAR"},
        "postedAt": "2026-01-01T00:00:00Z",
    }
    job = normalize_job(_raw(payload), _source(), company_name="acme", now=NOW)

    assert job.title == "Senior Backend Engineer"
    assert job.normalized_title == "senior backend engineer"
    assert job.title_family == "backend engineer"
    assert job.workplace_type is WorkplaceType.HYBRID
    assert job.employment_type is EmploymentType.FULL_TIME
    assert job.seniority is Seniority.SENIOR
    assert job.salary is not None and job.salary.min_amount == 50000
    assert job.posted_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert job.locations[0].raw == "Berlin, Germany"
    assert job.company_ref.name == "acme"
    assert job.provenance[0].strategy_id == "normalize.v1"
    assert job.provenance[0].source_id == "s1"


def test_workplace_inferred_from_merged_locations_field():
    # Extraction merges fields into a new payload, so the normalizer sees the
    # plural ``locations`` rather than the connector's singular ``location``.
    payload = {"locations": [{"raw": "Remote India"}]}
    job = normalize_job(_raw(payload), _source(), company_name="acme", now=NOW)
    assert job.workplace_type is WorkplaceType.REMOTE
    assert job.locations[0].raw == "Remote India"


def test_workplace_inferred_from_merged_locations_hybrid():
    payload = {"locations": [{"raw": "Budapest, Hungary (Hybrid)"}]}
    job = normalize_job(_raw(payload), _source(), company_name="acme", now=NOW)
    assert job.workplace_type is WorkplaceType.HYBRID


def test_explicit_workplace_wins_over_location_text():
    payload = {"workplaceType": "onsite", "locations": [{"raw": "Remote"}]}
    job = normalize_job(_raw(payload), _source(), company_name="acme", now=NOW)
    assert job.workplace_type is WorkplaceType.ONSITE


def test_normalize_job_marks_missing_fields_partial():
    job = normalize_job(_raw({}), _source(), company_name="acme", now=NOW)
    assert job.salary is None
    assert job.posted_at is None
    assert job.workplace_type is WorkplaceType.UNKNOWN
    assert job.extraction_status.value == "partial"


def test_content_hash_changes_with_content():
    first = content_hash_for("Title", "Body")
    assert first == content_hash_for("Title", "Body")
    assert first != content_hash_for("Title", "Other body")
