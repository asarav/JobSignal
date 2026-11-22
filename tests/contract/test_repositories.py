"""Repository contract tests run against both SQLite and InMemory UoW."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from app.domain.models.candidate import SearchProfile
from app.domain.models.common import Provenance
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import SourceConfig
from app.domain.models.tasks import BackgroundTask, TaskStatus
from app.domain.services.normalizer import content_hash_for, normalize_job
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory


async def _make_uow_factory(kind, tmp_path):
    if kind == "memory":
        return InMemoryUnitOfWorkFactory()
    if kind == "sqlite":
        db = tmp_path / "test.sqlite"

        from app.infrastructure.persistence.sqlite.migrator import migrate

        await migrate(db)
        return SqliteUnitOfWorkFactory(db_path=db, clock_now=datetime.now)
    raise ValueError(kind)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_settings_roundtrip(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    async with factory() as uow:
        await uow.settings.put("foo", {"bar": 1})
        await uow.commit()
    async with factory() as uow:
        got = await uow.settings.get("foo")
        assert got == {"bar": 1}
        all_settings = await uow.settings.get_all()
        assert "foo" in all_settings


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_task_add_get_list(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    task = BackgroundTask(
        id="t1", type="diagnostics.sleep", status=TaskStatus.QUEUED, priority=5,
        params={}, progress={}, error=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    async with factory() as uow:
        await uow.tasks.add(task)
        await uow.commit()
    async with factory() as uow:
        got = await uow.tasks.get("t1")
        assert got is not None
        assert got.status == TaskStatus.QUEUED
        listed = await uow.tasks.list()
        assert len(listed) >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_task_cancel_noop_unknown(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    async with factory() as uow:
        # unknown task cancel is no-op success
        await uow.tasks.get("does-not-exist")  # returns None, no error
        listed = await uow.tasks.list()
        assert listed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_search_profile_add_get_list(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    profile = SearchProfile(
        id="sp_1",
        name="Python Remote",
        candidate_profile_id="cp_1",
        scoring_config_id="sc_1",
        ranking_policy="deprioritize",
        hard_filters={"workplace_types": ["remote"]},
        preferences={"key": "value"},
    )
    async with factory() as uow:
        await uow.search_profiles.add(profile)
        await uow.commit()

    async with factory() as uow:
        got = await uow.search_profiles.get("sp_1")
        assert got is not None
        assert got.name == "Python Remote"
        assert got.hard_filters == {"workplace_types": ["remote"]}
        assert got.preferences == {"key": "value"}

        listed = await uow.search_profiles.list()
        assert len(listed) == 1
        assert listed[0].name == "Python Remote"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_search_profile_save_update(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    profile = SearchProfile(
        id="sp_1",
        name="Original",
        candidate_profile_id="",
        hard_filters={},
        preferences={},
    )
    async with factory() as uow:
        await uow.search_profiles.add(profile)
        await uow.commit()

    async with factory() as uow:
        got = await uow.search_profiles.get("sp_1")
        assert got is not None
        got = SearchProfile(
            id=got.id,
            name="Updated",
            candidate_profile_id=got.candidate_profile_id,
            scoring_config_id=got.scoring_config_id,
            ranking_policy=got.ranking_policy,
            hard_filters={"employment_types": ["contract"]},
            preferences=got.preferences,
        )
        await uow.search_profiles.save(got)
        await uow.commit()

    async with factory() as uow:
        got = await uow.search_profiles.get("sp_1")
        assert got is not None
        assert got.name == "Updated"
        assert got.hard_filters == {"employment_types": ["contract"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_search_profile_delete(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    profile = SearchProfile(id="sp_1", name="To Delete", candidate_profile_id="")
    async with factory() as uow:
        await uow.search_profiles.add(profile)
        await uow.commit()

    async with factory() as uow:
        await uow.search_profiles.delete("sp_1")
        await uow.commit()
        got = await uow.search_profiles.get("sp_1")
        assert got is None
        listed = await uow.search_profiles.list()
        assert listed == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_search_profile_get_unknown(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    async with factory() as uow:
        got = await uow.search_profiles.get("does-not-exist")
        assert got is None

@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_resume_file_lookup_and_version_file_details(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    async with factory() as uow:
        await uow.resumes.add_profile("default", "Default")
        await uow.resumes.add_resume_file(
            "rf_1", "abc123", "cv.txt", "text/plain", 12, "/data/resumes/ab/abc123"
        )
        await uow.resumes.add_resume_version(
            "rv_1", "default", "rf_1", "Imported", "txt", "1", "hash", "{}", is_active=True
        )
        await uow.commit()

    async with factory() as uow:
        found = await uow.resumes.get_resume_file_by_sha256("abc123")
        assert found is not None
        assert found["id"] == "rf_1"
        assert await uow.resumes.get_resume_file_by_sha256("missing") is None

        rows = await uow.resumes.list_versions("default")
        assert len(rows) == 1
        assert rows[0]["original_name"] == "cv.txt"
        assert rows[0]["stored_path"] == "/data/resumes/ab/abc123"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_resume_skills_replace_previous_set(kind, tmp_path):
    """Saving skills again replaces the set; removed skills must not linger."""
    factory = await _make_uow_factory(kind, tmp_path)
    async with factory() as uow:
        await uow.resumes.add_profile("default", "Default")
        await uow.resumes.add_resume_file(
            "rf_1", "abc123", "cv.txt", "text/plain", 12, "/data/resumes/ab/abc123"
        )
        await uow.resumes.add_resume_version(
            "rv_1", "default", "rf_1", "Imported", "txt", "1", "hash", "{}", is_active=True
        )
        await uow.resumes.save_skills(
            "rv_1",
            [
                {"keyword_id": "python", "evidence": "explicit", "confidence": 0.8},
                {"keyword_id": "sql", "evidence": "explicit", "confidence": 0.8},
            ],
        )
        await uow.commit()

    async with factory() as uow:
        await uow.resumes.save_skills(
            "rv_1",
            [{"keyword_id": "python", "evidence": "explicit_with_experience", "confidence": 0.9}],
        )
        await uow.commit()
        rows = await uow.resumes.get_skills("rv_1")

    assert {str(row["keyword_id"]) for row in rows} == {"python"}
    assert rows[0]["evidence"] == "explicit_with_experience"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_crawl_run_last_started_by_source(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    later = datetime(2026, 1, 2, tzinfo=UTC)
    async with factory() as uow:
        await uow.sources.add(SourceConfig(id="src_a", url="https://example.com/a", kind="ats"))
        await uow.sources.add(SourceConfig(id="src_b", url="https://example.com/b", kind="ats"))
        await uow.crawl_runs.create("run_1", "src_a", earlier)
        await uow.crawl_runs.create("run_2", "src_a", later)
        await uow.crawl_runs.create("run_3", "src_b", earlier)
        await uow.commit()

    async with factory() as uow:
        latest = await uow.crawl_runs.last_started_by_source()

    assert set(latest) == {"src_a", "src_b"}
    assert latest["src_a"].startswith("2026-01-02")
    assert latest["src_b"].startswith("2026-01-01")


def _via_index_job(source: SourceConfig, suffix: str = "1"):
    now = datetime(2026, 1, 1, tzinfo=UTC)
    url = f"https://example.com/a/{suffix}"
    raw = RawJob(
        ref=RawJobRef(source_id=source.id, url=url),
        title="Engineer",
        body_text="python",
        body_html=None,
        payload={},
    )
    job = normalize_job(raw, source, company_name="Acme", now=now)
    note = Provenance(
        source_id=source.id,
        strategy_id="discovery.funnel",
        plugin_id="discovery.funnel",
        plugin_version="1.0.0",
        url=url,
        fetched_at=now,
        content_hash=content_hash_for("Engineer", "python"),
        notes=("via_index",),
    )
    return replace(job, provenance=(*job.provenance, note))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_jobs_grouped_by_source_and_provenance_note_clearing(kind, tmp_path):
    factory = await _make_uow_factory(kind, tmp_path)
    source = SourceConfig(id="src_a", url="https://example.com/a", kind="career_site")
    async with factory() as uow:
        await uow.sources.add(source)
        await uow.companies.upsert("co_acme", "Acme", normalized_name="acme")
        for job_id, suffix in (("job_1", "1"), ("job_2", "2")):
            await uow.jobs.upsert(
                job_id,
                company_id="co_acme",
                source_id=source.id,
                job=_via_index_job(source, suffix),
                content_hash=content_hash_for("Engineer", "python"),
            )
        await uow.commit()

    async with factory() as uow:
        grouped = await uow.jobs.list_for_sources(["src_a", "src_missing"], limit=1)
        assert len(grouped["src_a"]) == 1, "the per-source limit applies"
        assert list(grouped["src_missing"]) == []
        assert await uow.jobs.clear_provenance_note("job_1", "via_index") is True
        assert await uow.jobs.clear_provenance_note("job_1", "via_index") is False
        await uow.commit()

    async with factory() as uow:
        rows = await uow.jobs.list(source_id="src_a")
        by_id = {str(row["id"]): row for row in rows}

    def notes_of(job_id: str) -> list[list[str]]:
        return [
            list(entry.get("notes") or [])
            for entry in by_id[job_id]["provenance"]
        ]

    assert len(notes_of("job_1")) == 2, "only the note goes, not the provenance"
    assert all("via_index" not in notes for notes in notes_of("job_1"))
    assert any("via_index" in notes for notes in notes_of("job_2"))
