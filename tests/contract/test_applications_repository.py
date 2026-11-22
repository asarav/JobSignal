"""Application, suppression and user-state repositories (memory + SQLite)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.domain.models.application import (
    Application,
    ApplicationEvent,
    ApplicationOutcome,
    SuppressionRecord,
)
from app.domain.models.common import Provenance
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import SourceConfig
from app.domain.services.normalizer import content_hash_for, normalize_job
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory

NOW = datetime(2026, 3, 1, tzinfo=UTC)


async def _factory(kind: str, tmp_path):
    if kind == "memory":
        return InMemoryUnitOfWorkFactory()
    if kind == "sqlite":
        db = tmp_path / "test.sqlite"

        from app.infrastructure.persistence.sqlite.migrator import migrate

        await migrate(db)
        return SqliteUnitOfWorkFactory(db_path=db, clock_now=lambda: NOW)
    raise ValueError(kind)


def _normalized(source: SourceConfig, url: str):
    raw = RawJob(
        ref=RawJobRef(source_id=source.id, url=url),
        title="Backend Engineer",
        body_text="python distributed systems",
        body_html=None,
        payload={},
    )
    job = normalize_job(raw, source, company_name="Acme", now=NOW)
    note = Provenance(
        source_id=source.id,
        strategy_id="extract.static_html",
        plugin_id="extract.static_html",
        plugin_version="1.0.0",
        url=url,
        fetched_at=NOW,
        content_hash=content_hash_for("Backend Engineer", "python distributed systems"),
    )
    from dataclasses import replace as dc_replace

    return dc_replace(job, provenance=(*job.provenance, note))


async def _seed_job(uow, job_id: str = "job_1", source_id: str = "src_a") -> None:
    source = SourceConfig(id=source_id, url="https://example.com/a", kind="career_site")
    if await uow.sources.get(source_id) is None:
        await uow.sources.add(source)
    await uow.companies.upsert("co_acme", "Acme", normalized_name="acme")
    await uow.jobs.upsert(
        job_id,
        company_id="co_acme",
        source_id=source_id,
        job=_normalized(source, f"https://example.com/a/{job_id}"),
        content_hash=content_hash_for("Backend Engineer", "python distributed systems"),
    )


def _application(application_id: str = "app_1", **over: object) -> Application:
    values: dict[str, object] = {
        "id": application_id,
        "company_id": "co_acme",
        "job_id": "job_1",
        "job_title": "Backend Engineer",
        "job_title_family": "engineering",
        "job_canonical_url": "https://example.com/a/job_1",
        "job_description_text": "python distributed systems",
        "job_department": "Platform",
        "job_locations": ({"city": "Berlin"},),
        "job_workplace_type": "remote",
        "job_snapshot_id": None,
        "status": "applied",
        "applied_at": NOW,
        "resume_version_id": None,
        "notes": "",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(over)
    return Application(**values)  # type: ignore[arg-type]


def _record(job_id: str = "job_2", application_id: str = "app_1", **over: object):
    values: dict[str, object] = {
        "id": f"sup_{job_id}",
        "job_id": job_id,
        "application_id": application_id,
        "state": "active",
        "reason": "similar_role_cooldown",
        "explanation": "similar role",
        "similarity": 0.9,
        "evidence": {"similarity_breakdown": {"score": 0.9}},
        "policy": {"similar_role_cooldown_days": 180},
        "expires_at": datetime(2026, 8, 1, tzinfo=UTC),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(over)
    return SuppressionRecord(**values)  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_application_roundtrip_with_events_and_outcomes(kind, tmp_path):
    factory = await _factory(kind, tmp_path)
    async with factory() as uow:
        await _seed_job(uow)
        await uow.applications.add(_application())
        await uow.applications.add_event(
            ApplicationEvent(
                id="ev_1",
                application_id="app_1",
                kind="created",
                at=NOW,
                to_status="applied",
                note="applied today",
            )
        )
        await uow.applications.add_outcome(
            ApplicationOutcome(
                id="out_1",
                application_id="app_1",
                kind="interview",
                occurred_at=NOW,
                note="first round",
            )
        )
        await uow.commit()

    async with factory() as uow:
        got = await uow.applications.get("app_1")
        assert got is not None
        assert got.status == "applied"
        assert got.applied_at.replace(tzinfo=None) == NOW.replace(tzinfo=None)
        assert await uow.applications.find_for_job("job_1") == [got]

        events = await uow.applications.list_events("app_1")
        assert [event.kind for event in events] == ["created"]
        assert events[0].note == "applied today"

        outcomes = await uow.applications.list_outcomes("app_1")
        assert [outcome.kind for outcome in outcomes] == ["interview"]

        applied = await uow.applications.list_applied()
        assert len(applied) == 1
        assert applied[0].title == "Backend Engineer"
        assert applied[0].workplace_type


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_save_status_clears_applied_at_for_pre_application_state(kind, tmp_path):
    factory = await _factory(kind, tmp_path)
    async with factory() as uow:
        await _seed_job(uow)
        await uow.applications.add(_application())
        await uow.commit()

    async with factory() as uow:
        saved = await uow.applications.save(
            "app_1",
            status="saved",
            applied_at=None,
            notes="changed my mind",
            resume_version_id=None,
            updated_at=NOW,
        )
        await uow.commit()
        assert saved is not None
        assert saved.applied_at is None
        assert await uow.applications.list_applied() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_suppression_state_machine_and_restore_stickiness(kind, tmp_path):
    factory = await _factory(kind, tmp_path)
    async with factory() as uow:
        await _seed_job(uow)
        await _seed_job(uow, "job_2")
        await uow.applications.add(_application())
        await uow.suppressions.save(_record())
        await uow.commit()

    async with factory() as uow:
        assert await uow.suppressions.active_job_ids(NOW) == ["job_2"]
        assert await uow.suppressions.restore("job_2", "app_1", NOW) is True
        await uow.commit()

    async with factory() as uow:
        stored = await uow.suppressions.get("job_2", "app_1")
        assert stored is not None and stored.state == "restored"
        # Automation must not silently re-suppress a restored pair.
        await uow.suppressions.save(_record())
        await uow.commit()

    async with factory() as uow:
        stored = await uow.suppressions.get("job_2", "app_1")
        assert stored is not None and stored.state == "restored"
        assert await uow.suppressions.active_job_ids(NOW) == []
        # Clearing the restore marker is the only way back.
        assert await uow.suppressions.clear_restored("job_2", "app_1") is True
        await uow.suppressions.save(_record())
        await uow.commit()

    async with factory() as uow:
        assert await uow.suppressions.active_job_ids(NOW) == ["job_2"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_expire_only_lapses_due_decisions(kind, tmp_path):
    factory = await _factory(kind, tmp_path)
    async with factory() as uow:
        await _seed_job(uow)
        await _seed_job(uow, "job_2")
        await _seed_job(uow, "job_3")
        await uow.applications.add(_application())
        await uow.suppressions.save(_record("job_2"))
        await uow.suppressions.save(
            _record("job_3", expires_at=datetime(2027, 1, 1, tzinfo=UTC))
        )
        await uow.commit()

    async with factory() as uow:
        lapsed = await uow.suppressions.expire(datetime(2026, 9, 1, tzinfo=UTC))
        await uow.commit()
        assert [record.job_id for record in lapsed] == ["job_2"]

    async with factory() as uow:
        assert await uow.suppressions.active_job_ids(NOW) == ["job_3"]
        states = {record.job_id: record.state for record in await uow.suppressions.list()}
        assert states["job_2"] == "expired"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_clear_job_keeps_restored_rows(kind, tmp_path):
    factory = await _factory(kind, tmp_path)
    async with factory() as uow:
        await _seed_job(uow)
        await _seed_job(uow, "job_2")
        await uow.applications.add(_application())
        await uow.suppressions.save(_record("job_2"))
        await uow.suppressions.restore("job_2", "app_1", NOW)
        await uow.commit()

    async with factory() as uow:
        assert await uow.suppressions.clear_job("job_2") == 0
        assert len(await uow.suppressions.list_for_job("job_2")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_policy_roundtrip_replaces_the_default_row(kind, tmp_path):
    factory = await _factory(kind, tmp_path)
    async with factory() as uow:
        assert await uow.suppressions.get_policy() is None
        await uow.suppressions.put_policy({"similar_role_cooldown_days": 30}, NOW)
        await uow.suppressions.put_policy({"similar_role_cooldown_days": 60}, NOW)
        await uow.commit()

    async with factory() as uow:
        stored = await uow.suppressions.get_policy()
        assert stored == {"similar_role_cooldown_days": 60}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_job_user_state_hides_jobs_from_default_lists(kind, tmp_path):
    factory = await _factory(kind, tmp_path)
    async with factory() as uow:
        await _seed_job(uow)
        await _seed_job(uow, "job_2")
        await uow.job_user_state.set("job_2", "dismissed", note="not for me", updated_at=NOW)
        await uow.commit()

    async with factory() as uow:
        assert await uow.job_user_state.hidden_job_ids() == ["job_2"]
        assert await uow.job_user_state.get_many(["job_1", "job_2"]) == {
            "job_2": "dismissed"
        }
        visible = await uow.jobs.list(exclude_job_ids=["job_2"])
        assert [str(row["id"]) for row in visible] == ["job_1"]
        page, total = await uow.jobs.filtered_page(
            {}, exclude_job_ids=["job_2"]
        )
        assert total == 1
        assert [str(row["id"]) for row in page] == ["job_1"]
        assert await uow.jobs.count(exclude_job_ids=["job_2"]) == 1
        assert await uow.job_user_state.clear("job_2") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_a_lapsed_cooldown_stops_hiding_its_job_without_a_sweep(kind, tmp_path):
    """The read path must honour ``expires_at``, not just the stored state.

    A cooldown that ended while the app was idle used to keep hiding the job
    until someone opened Previously Seen and triggered the sweep.
    """
    factory = await _factory(kind, tmp_path)
    async with factory() as uow:
        await _seed_job(uow)
        await _seed_job(uow, "job_2")
        await uow.applications.add(_application())
        await uow.suppressions.save(
            _record("job_2", expires_at=datetime(2026, 2, 1, tzinfo=UTC))
        )
        await uow.commit()

    async with factory() as uow:
        assert await uow.suppressions.active_job_ids(NOW) == []
        stored = await uow.suppressions.get("job_2", "app_1")
        assert stored is not None and stored.state == "active"
