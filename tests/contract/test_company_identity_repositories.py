"""Company merge and keyword repositories behave identically in memory and SQLite."""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite
import pytest
from app.domain.models.company_merge import CompanyMergeRecord
from app.domain.models.job_keyword import (
    JobKeywordRecord,
    JobKeywordRevisionRecord,
)
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory

NOW = datetime(2026, 3, 1, tzinfo=UTC)


async def _factory(kind: str, tmp_path) -> object:
    if kind == "memory":
        factory = InMemoryUnitOfWorkFactory()
        for company_id, name in (("co_a", "Acme"), ("co_b", "Acme Inc"), ("co_c", "Acme Corp")):
            factory.companies[company_id] = {
                "id": company_id,
                "canonical_name": name,
                "normalized_name": name.lower(),
            }
        factory.jobs["job_1"] = {
            "id": "job_1",
            "company_id": "co_b",
            "source_id": "src_1",
            "title": "Data Engineer",
            "first_seen_at": "2026-01-01T00:00:00Z",
        }
        factory.jobs["job_2"] = {
            "id": "job_2",
            "company_id": "co_a",
            "source_id": "src_1",
            "title": "Analyst",
            "first_seen_at": "2026-01-02T00:00:00Z",
        }
        return factory
    db = tmp_path / "identity.sqlite"
    factory = SqliteUnitOfWorkFactory(db_path=db, clock_now=lambda: NOW)
    await factory.initialize()
    async with aiosqlite.connect(db) as conn:
        for company_id, name in (("co_a", "Acme"), ("co_b", "Acme Inc"), ("co_c", "Acme Corp")):
            await conn.execute(
                "INSERT INTO companies(id, canonical_name, normalized_name, created_at) "
                "VALUES (?, ?, ?, ?)",
                (company_id, name, name.lower(), "2026-01-01T00:00:00Z"),
            )
        await conn.execute(
            "INSERT INTO sources(id, url, kind, created_at, updated_at) "
            "VALUES ('src_1', 'https://example.com', 'ats', ?, ?)",
            ("2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        for job_id, company_id, title, seen in (
            ("job_1", "co_b", "Data Engineer", "2026-01-01T00:00:00Z"),
            ("job_2", "co_a", "Analyst", "2026-01-02T00:00:00Z"),
        ):
            await conn.execute(
                "INSERT INTO jobs(id, company_id, source_id, title, normalized_title, "
                "canonical_url, first_seen_at, last_seen_at, fetched_at, created_at, "
                "updated_at) VALUES (?, ?, 'src_1', ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    job_id,
                    company_id,
                    title,
                    title.lower(),
                    f"https://example.com/{job_id}",
                    seen,
                    seen,
                    seen,
                    seen,
                    seen,
                ),
            )
        await conn.commit()
    return factory


def _merge(id_: str, merged: str, survivor: str, action: str = "merge") -> CompanyMergeRecord:
    return CompanyMergeRecord(
        id=id_,
        survivor_id=survivor,
        merged_id=merged,
        action=action,
        created_at=NOW,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_the_newest_decision_per_company_wins(kind, tmp_path) -> None:
    factory = await _factory(kind, tmp_path)
    async with factory() as uow:
        await uow.company_merges.add(_merge("cm_1", "co_b", "co_a"))
        await uow.company_merges.add(_merge("cm_2", "co_b", "co_a", action="unmerge"))
        await uow.company_merges.add(_merge("cm_3", "co_c", "co_a"))
        await uow.commit()

    async with factory() as uow:
        latest = {row["merged_id"]: row for row in await uow.company_merges.latest_actions()}
        history = await uow.company_merges.list_all()

    assert latest["co_b"]["action"] == "unmerge"
    assert latest["co_c"]["action"] == "merge"
    assert [row["id"] for row in history] == ["cm_3", "cm_2", "cm_1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_history_for_one_company_covers_both_sides(kind, tmp_path) -> None:
    factory = await _factory(kind, tmp_path)
    async with factory() as uow:
        await uow.company_merges.add(_merge("cm_1", "co_b", "co_a"))
        await uow.company_merges.add(_merge("cm_2", "co_c", "co_a"))
        await uow.commit()

    async with factory() as uow:
        as_survivor = await uow.company_merges.list_for_company("co_a")
        as_merged = await uow.company_merges.list_for_company("co_b")

    assert len(as_survivor) == 2
    assert [row["id"] for row in as_merged] == ["cm_1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_keyword_revisions_are_idempotent_and_keep_history(kind, tmp_path) -> None:
    factory = await _factory(kind, tmp_path)

    def revision(id_: str, hash_: str) -> JobKeywordRevisionRecord:
        return JobKeywordRevisionRecord(
            id=id_,
            job_id="job_1",
            revision_hash=hash_,
            taxonomy_version="taxonomy.v1",
            extracted_at=NOW,
            keyword_count=2,
        )

    def keyword(id_: str, hash_: str, keyword_id: str) -> JobKeywordRecord:
        return JobKeywordRecord(
            id=id_,
            job_id="job_1",
            revision_hash=hash_,
            keyword_id=keyword_id,
            taxonomy_version="taxonomy.v1",
            extracted_at=NOW,
        )

    async with factory() as uow:
        await uow.keywords.add_revision(
            revision("rev_1", "h1"), [keyword("jk_1", "h1", "python"), keyword("jk_2", "h1", "sql")]
        )
        await uow.keywords.add_revision(
            revision("rev_1", "h1"), [keyword("jk_1", "h1", "python")]
        )
        await uow.keywords.add_revision(
            revision("rev_2", "h2"), [keyword("jk_3", "h2", "go")]
        )
        await uow.commit()

    async with factory() as uow:
        latest = await uow.keywords.latest_revision("job_1")
        batched = await uow.keywords.latest_revisions_for_jobs(["job_1", "job_2"])
        keywords = await uow.keywords.keywords_for_jobs(["job_1", "job_2"])
        mentions = await uow.keywords.keywords_with_mentions(["job_1"])
        with_revisions = await uow.keywords.jobs_with_revisions()
        missing = await uow.keywords.jobs_missing_revisions()

    assert latest is not None and latest["revision_hash"] == "h2"
    assert set(batched) == {"job_1"}
    assert keywords["job_1"] == ["go"], "only the latest revision is reported"
    assert mentions["job_1"] == {"go": 1}
    assert with_revisions == ["job_1"]
    assert missing == ["job_2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_keywords_can_be_scoped_by_company(kind, tmp_path) -> None:
    factory = await _factory(kind, tmp_path)
    async with factory() as uow:
        for job_id in ("job_1", "job_2"):
            await uow.keywords.add_revision(
                JobKeywordRevisionRecord(
                    id=f"rev_{job_id}",
                    job_id=job_id,
                    revision_hash="h1",
                    taxonomy_version="taxonomy.v1",
                    keyword_count=1,
                    extracted_at=NOW,
                ),
                [],
            )
        await uow.commit()

    async with factory() as uow:
        scoped = await uow.keywords.jobs_with_revisions(company_ids=["co_a"])

    assert scoped == ["job_2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_keyword_rows_cover_every_stored_revision(kind, tmp_path) -> None:
    factory = await _factory(kind, tmp_path)
    async with factory() as uow:
        for job_id, hash_ in (("job_1", "h1"), ("job_1", "h2"), ("job_2", "h9")):
            await uow.keywords.add_revision(
                JobKeywordRevisionRecord(
                    id=f"rev_{job_id}_{hash_}",
                    job_id=job_id,
                    revision_hash=hash_,
                    taxonomy_version="taxonomy.v1",
                    keyword_count=1,
                    extracted_at=NOW,
                ),
                [
                    JobKeywordRecord(
                        id=f"jk_{job_id}_{hash_}",
                        job_id=job_id,
                        revision_hash=hash_,
                        keyword_id="python",
                        taxonomy_version="taxonomy.v1",
                        extracted_at=NOW,
                        mentions=2,
                    )
                ],
            )
        await uow.commit()

    async with factory() as uow:
        rows = await uow.keywords.keyword_rows_for_jobs(["job_1", "job_2", "missing"])

    assert {
        (str(row["job_id"]), str(row["revision_hash"]), int(row["mentions"]))
        for row in rows
    } == {
        ("job_1", "h1", 2),
        ("job_1", "h2", 2),
        ("job_2", "h9", 2),
    }


@pytest.mark.asyncio
async def test_sqlite_merge_and_keyword_history_is_immutable(tmp_path) -> None:
    factory = await _factory("sqlite", tmp_path)
    async with factory() as uow:
        await uow.company_merges.add(_merge("cm_1", "co_b", "co_a"))
        await uow.keywords.add_revision(
            JobKeywordRevisionRecord(
                id="rev_1",
                job_id="job_1",
                revision_hash="h1",
                taxonomy_version="taxonomy.v1",
                extracted_at=NOW,
            ),
            [],
        )
        await uow.commit()

    db = tmp_path / "identity.sqlite"
    async with aiosqlite.connect(db) as conn:
        with pytest.raises(aiosqlite.Error):
            await conn.execute("UPDATE company_merges SET action = 'unmerge'")
        with pytest.raises(aiosqlite.Error):
            await conn.execute("DELETE FROM company_merges")
        with pytest.raises(aiosqlite.Error):
            await conn.execute("UPDATE job_keyword_revisions SET revision_hash = 'x'")
        with pytest.raises(aiosqlite.Error):
            await conn.execute("DELETE FROM job_keyword_revisions")
