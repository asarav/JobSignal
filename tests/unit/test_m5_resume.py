"""Unit tests for M5 resume parsing / keywords."""
from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.application.commands.resume import ResumeUseCase
from app.domain.models.resume import ResumeFileRef
from app.domain.services.keywords import KeywordExtractor, KeywordNormalizer
from app.domain.services.sectioner import (
    compute_skill_tenure,
    compute_years_experience,
    extract_contact,
    extract_dates,
    extract_education_entries,
    extract_experience_entries,
    extract_profile_items,
    parse_date_token,
    parse_experience_header,
    section_text,
)
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from app.plugins.resume.txt import TxtResumeParser
from tests.support.fakes import FakeClock

RESUME = """Jane Doe
jane@example.com
+1 (555) 123-4567
linkedin.com/in/janedoe

Summary
Backend engineer focused on AWS.

Skills
Python, SQL, Docker

Experience
Acme Corp
Senior Engineer Jan 2020 - Jan 2022
- Built Python services on AWS.

Beta Inc
Engineer 2021 - 2022
- Shipped internal tools.
"""


def test_keyword_extraction() -> None:
    text = "We need a Python developer with React and AWS experience."
    skills = KeywordExtractor().extract(text)
    assert "python" in skills
    assert "react" in skills
    assert "aws" in skills


def test_keyword_mentions_exclude_implications() -> None:
    extractor = KeywordExtractor()
    assert extractor.mentions("Django services") == ["django"]
    assert "python" in extractor.extract("Django services")


def test_keyword_normalizer_alias() -> None:
    n = KeywordNormalizer()
    assert n.normalize("reactjs") == "react"
    assert n.normalize("golang") == "go"


def test_user_added_skills_normalize_through_taxonomy() -> None:
    from app.domain.services.keywords import normalize_skill_ids

    assert normalize_skill_ids(["ReactJS", "Kubernetes"]) == ["react", "kubernetes"]
    assert normalize_skill_ids(None) == []


def test_sectioner() -> None:
    text = "Summary\nI am an engineer.\n\nSkills\nPython, SQL\n\nExperience\n- Built things"
    secs = section_text(text)
    assert "skills" in secs
    assert "Python" in secs["skills"] or "python" in secs["skills"].lower()


def test_contact_extraction() -> None:
    contact = extract_contact(RESUME)
    assert contact["name"] == "Jane Doe"
    assert contact["email"] == "jane@example.com"
    assert contact["linkedin"] == "linkedin.com/in/janedoe"
    assert "555" in contact["phone"]


def test_year_ranges_are_not_phones() -> None:
    contact = extract_contact("Acme Corp\nSenior Engineer 2020 - 2023\n")
    assert "phone" not in contact


def test_date_tokens() -> None:
    assert parse_date_token("Jan 2020") == parse_date_token("January 2020")
    assert parse_date_token("01/2020") == parse_date_token("Jan 2020")
    assert parse_date_token("2020") is not None
    assert parse_date_token("Python 3") is None
    assert extract_dates("Python 3 - 5 years") == []
    assert extract_dates("Jan 2020 - Present") == [("Jan 2020", "Present")]


def test_experience_entries_split_on_dates() -> None:
    sections = section_text(RESUME)
    entries = extract_experience_entries(sections["experience"])
    assert len(entries) == 2
    assert "Acme" in entries[0]["header"]
    assert "Shipped internal tools." in entries[1]["bullets"]


def test_bullets_after_blank_line_stay_with_entry() -> None:
    text = "Acme Corp\nSenior Engineer Jan 2020 - Jan 2022\n\n- Built Python services.\n"
    entries = extract_experience_entries(text)
    assert len(entries) == 1
    assert "Built Python services." in entries[0]["bullets"]


def test_years_merge_overlapping_ranges() -> None:
    entries = extract_experience_entries(section_text(RESUME)["experience"])
    years = compute_years_experience(entries)
    assert years is not None
    assert 2.0 <= years <= 2.2
    assert compute_years_experience([]) is None


async def test_txt_parser() -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("Hello resume")
        path = f.name
    parser = TxtResumeParser()
    ref = ResumeFileRef(path=path, mime_type="text/plain", original_name="r.txt")
    res = await parser.parse(ref)
    from app.domain.models.common import Ok

    assert isinstance(res, Ok)
    assert "Hello resume" in res.value.text
    Path(path).unlink(missing_ok=True)


async def test_import_evidence_levels_and_contact(tmp_path: Path) -> None:
    factory = InMemoryUnitOfWorkFactory()
    container = SimpleNamespace(uow_factory=factory, clock=FakeClock())
    use_case = ResumeUseCase(container, tmp_path)  # type: ignore[arg-type]
    rpath = tmp_path / "resume.txt"
    rpath.write_text(RESUME, encoding="utf-8")

    result = await use_case.import_resume(str(rpath), "v1")
    assert result["contact"]["email"] == "jane@example.com"
    assert result["years_experience"] is not None

    async with factory() as uow:
        rows = await uow.resumes.get_skills(result["version_id"])
        skills = {str(row["keyword_id"]): str(row["evidence"]) for row in rows}
        profile = await uow.resumes.get_profile("default")
        overrides = await uow.resumes.get_overrides("default")
        await uow.commit()

    assert skills["python"] == "explicit_with_experience"
    assert skills["docker"] == "explicit"
    assert overrides["years_experience_total"] == pytest.approx(result["years_experience"])
    assert profile is not None
    assert "jane@example.com" in str(profile["contact_json"])


async def test_import_same_file_twice_sqlite(tmp_path: Path) -> None:
    """Regression: resume_files.sha256 is UNIQUE, so a re-import must reuse the row."""
    import hashlib

    from app.infrastructure.persistence.sqlite.migrator import migrate
    from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory

    db = tmp_path / "resume.sqlite"
    await migrate(db)
    factory = SqliteUnitOfWorkFactory(db_path=db, clock_now=FakeClock().now)
    container = SimpleNamespace(uow_factory=factory, clock=FakeClock())
    use_case = ResumeUseCase(container, tmp_path / "data")  # type: ignore[arg-type]
    rpath = tmp_path / "resume.txt"
    rpath.write_text(RESUME, encoding="utf-8")
    # Hash the bytes on disk: write_text may translate newlines (Windows).
    sha = hashlib.sha256(rpath.read_bytes()).hexdigest()

    first = await use_case.import_resume(str(rpath), "v1")
    second = await use_case.import_resume(str(rpath), "v2")

    assert first["version_id"] != second["version_id"]
    versions = await use_case.list_versions("default")
    assert {str(v["label"]) for v in versions} == {"v1", "v2"}
    for version in versions:
        stored = Path(str(version["stored_path"]))
        assert stored.exists()
        assert stored.name == sha

    async with factory() as uow:
        row = await uow.resumes.get_resume_file_by_sha256(sha)
        await uow.commit()
    assert row is not None
    assert row["sha256"] == sha


def test_experience_header_splits_employer_and_title() -> None:
    assert parse_experience_header("Acme Corp Senior Engineer") == (
        "Acme Corp",
        "Senior Engineer",
    )
    assert parse_experience_header("Senior Backend Engineer, Acme Corp") == (
        "Acme Corp",
        "Senior Backend Engineer",
    )
    assert parse_experience_header("Acme Corp — Data Analyst | Jan 2020 - Jan 2022") == (
        "Acme Corp",
        "Data Analyst",
    )
    assert parse_experience_header("Freelance") == ("Freelance", None)


def test_education_entries() -> None:
    text = "BSc Computer Science\nUniversity of Edinburgh 2012 - 2016\n"
    entries = extract_education_entries(text)
    assert len(entries) == 1
    assert entries[0]["institution"] == "University of Edinburgh"
    assert entries[0]["degree"] == "BSc"
    assert entries[0]["field"] == "Computer Science"
    assert entries[0]["start"] == "2012"
    assert entries[0]["end"] == "2016"

    entry = extract_education_entries("University of Berlin\nMaster of Science in Physics")[0]
    assert entry["institution"] == "University of Berlin"
    assert entry["degree"] == "Master of Science"
    assert entry["field"] == "Physics"


def test_profile_items_keep_bullets_with_their_entry() -> None:
    items = extract_profile_items("AWS Certified Solutions Architect\n- Renewed 2025\n")
    assert items == [
        {"name": "AWS Certified Solutions Architect", "details": "Renewed 2025"}
    ]


def test_skill_tenure_merges_overlapping_roles() -> None:
    entries = extract_experience_entries(section_text(RESUME)["experience"])
    extractor = KeywordExtractor()
    duration, recency = compute_skill_tenure(
        entries, "python", extractor.mentions, today=__import__("datetime").date(2026, 1, 1)
    )
    assert duration == pytest.approx(2.0, abs=0.1)
    assert recency == pytest.approx(4.0, abs=0.1)
    assert compute_skill_tenure(entries, "kubernetes", extractor.mentions) == (None, None)


async def test_import_persists_profile_sections_and_evidence(tmp_path: Path) -> None:
    from app.infrastructure.persistence.sqlite.migrator import migrate
    from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory

    db = tmp_path / "sections.sqlite"
    await migrate(db)
    factory = SqliteUnitOfWorkFactory(db_path=db, clock_now=FakeClock().now)
    container = SimpleNamespace(uow_factory=factory, clock=FakeClock())
    use_case = ResumeUseCase(container, tmp_path / "data")  # type: ignore[arg-type]
    path = tmp_path / "resume.txt"
    path.write_text(
        RESUME
        + "\nEducation\nBSc Computer Science\nUniversity of Edinburgh 2012 - 2016\n"
        + "Certifications\nAWS Certified Solutions Architect\n"
        + "Projects\nJob Signal Aggregator\n- Python job matching service.\n",
        encoding="utf-8",
    )
    result = await use_case.import_resume(str(path), "v1")

    profile = await use_case.get_profile("default")
    assert profile is not None
    assert profile["experience"][0]["employer"] == "Acme Corp"
    assert profile["experience"][0]["title"] == "Senior Engineer"
    assert profile["education"][0]["institution"] == "University of Edinburgh"
    assert profile["certifications"][0]["name"] == "AWS Certified Solutions Architect"
    assert profile["projects"][0]["name"] == "Job Signal Aggregator"

    skills = {row["keyword_id"]: row for row in profile["skills"]}
    assert skills["python"]["evidence_refs"], "skill evidence must carry spans"
    assert skills["python"]["duration_years"] == pytest.approx(2.0, abs=0.1)
    assert skills["python"]["recency_years"] is not None
    assert result["education"] == 1


async def test_manual_experience_overrides_win_over_parser_output(tmp_path: Path) -> None:
    factory = InMemoryUnitOfWorkFactory()
    container = SimpleNamespace(uow_factory=factory, clock=FakeClock())
    use_case = ResumeUseCase(container, tmp_path)  # type: ignore[arg-type]
    path = tmp_path / "resume.txt"
    path.write_text(RESUME, encoding="utf-8")
    await use_case.import_resume(str(path), "v1")

    experience = [
        {"employer": "Acme", "title": "Staff Engineer", "start": "2020", "end": "2022"}
    ]
    await use_case.update_overrides(
        "default", {"experience": experience, "titles": ["Staff Engineer"]}
    )

    profile = await use_case.get_profile("default")
    assert profile is not None
    assert profile["experience"] == experience


async def test_import_preserves_manual_overrides(tmp_path: Path) -> None:
    factory = InMemoryUnitOfWorkFactory()
    container = SimpleNamespace(uow_factory=factory, clock=FakeClock())
    use_case = ResumeUseCase(container, tmp_path)  # type: ignore[arg-type]
    rpath = tmp_path / "resume.txt"
    rpath.write_text(RESUME, encoding="utf-8")

    async with factory() as uow:
        await uow.resumes.add_profile("default", "Default")
        await uow.resumes.update_overrides(
            "default", {"years_experience_total": 9.0, "locations": ["NYC"]}
        )
        await uow.commit()

    await use_case.import_resume(str(rpath), "v1")

    async with factory() as uow:
        overrides = await uow.resumes.get_overrides("default")
        await uow.commit()

    assert overrides["years_experience_total"] == 9.0
    assert overrides["locations"] == ["NYC"]
