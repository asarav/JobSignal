"""Posting-intelligence history behaves identically in memory and SQLite.

These cover the contract the pipeline relies on: append-only writes, "latest"
resolved by timestamp, batched reads, and the triggers that make rewriting
history impossible in the real database.
"""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite
import pytest
from app.domain.models.common import Evidence, Explanation
from app.domain.models.enums import RiskClass, SignalStatus
from app.domain.models.posting import (
    ClassificationChangeRecord,
    CompanySummaryRecord,
    EndpointObservationRecord,
    RiskAssessment,
    RiskAssessmentRecord,
    SignalObservationRecord,
    SignalResult,
)
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from app.infrastructure.persistence.sqlite.uow import SqliteUnitOfWorkFactory

NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _signal(
    signal_id: str = "signal.posting_age",
    *,
    risk: float = 0.4,
    confidence: float = 0.6,
    status: SignalStatus = SignalStatus.AVAILABLE,
    strong: bool = False,
) -> SignalResult:
    return SignalResult(
        signal_id=signal_id,
        provider_version="1.0.0",
        status=status,
        risk_contribution=risk if status is SignalStatus.AVAILABLE else None,
        confidence=confidence if status is SignalStatus.AVAILABLE else 0.0,
        strong=strong,
        evidence=(
            Evidence(
                kind="check",
                description="stored evidence",
                data={"value": 1},
                observed_at=NOW,
            ),
        ),
        missing_reason=None if status is SignalStatus.AVAILABLE else "no data",
        explanation="A cautious sentence.",
        evidence_source="tests",
        observed_at=NOW,
        evaluated_at=NOW,
    )


def _assessment(*, risk: float = 0.55, confidence: float = 0.52) -> RiskAssessment:
    risk_class = (
        RiskClass.LIKELY_STALE
        if risk >= 0.5 and confidence >= 0.4
        else RiskClass.NEEDS_VERIFICATION
    )
    return RiskAssessment(
        model_id="risk.heuristic_v1",
        model_version="1.0.0",
        risk=risk,
        confidence=confidence,
        risk_class=risk_class,
        explanation=Explanation(
            summary="Possibly stale posting (evidence: limited)", reasons=()
        ),
        signals=(_signal(),),
        evaluated_at=NOW,
        evidence_sufficiency="limited",
        config_hash="abc123",
        top_signals=("signal.posting_age",),
    )


async def _setup(kind: str, tmp_path) -> object:
    if kind == "memory":
        factory = InMemoryUnitOfWorkFactory()
        factory.companies["co_1"] = {
            "id": "co_1",
            "canonical_name": "Acme",
            "normalized_name": "acme",
        }
        factory.jobs["job_1"] = {
            "id": "job_1",
            "company_id": "co_1",
            "source_id": "src_1",
            "title": "Data Engineer",
            "posting_status": "active",
        }
        return factory
    db = tmp_path / "intelligence.sqlite"
    factory = SqliteUnitOfWorkFactory(db_path=db, clock_now=lambda: NOW)
    await factory.initialize()
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "INSERT INTO companies(id, canonical_name, normalized_name, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("co_1", "Acme", "acme", "2026-01-01T00:00:00Z"),
        )
        await conn.execute(
            "INSERT INTO sources(id, url, kind, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "src_1",
                "https://example.com/careers",
                "ats",
                "2026-01-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
            ),
        )
        await conn.execute(
            """
            INSERT INTO jobs(
              id, company_id, source_id, title, normalized_title, canonical_url,
              first_seen_at, last_seen_at, extraction_status,
              extraction_confidence, posting_status, fetched_at,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job_1",
                "co_1",
                "src_1",
                "Data Engineer",
                "data engineer",
                "https://example.com/jobs/1",
                "2026-01-01T00:00:00Z",
                "2026-02-01T00:00:00Z",
                "complete",
                0.9,
                "active",
                "2026-02-01T00:00:00Z",
                "2026-01-01T00:00:00Z",
                "2026-02-01T00:00:00Z",
            ),
        )
        await conn.commit()
    return factory


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_registry_upsert_is_idempotent(kind, tmp_path) -> None:
    factory = await _setup(kind, tmp_path)
    entry = {
        "signal_id": "signal.posting_age",
        "description": "How long the posting has been visible.",
        "provider_version": "1.0.0",
        "default_weight": 0.10,
    }
    async with factory() as uow:
        await uow.intelligence.signals.upsert_registry([entry])
        await uow.commit()
    async with factory() as uow:
        await uow.intelligence.signals.upsert_registry([entry])
        await uow.commit()
    async with factory() as uow:
        rows = await uow.intelligence.signals.list_registry()

    assert [row["signal_id"] for row in rows] == ["signal.posting_age"]
    assert float(rows[0]["default_weight"]) == pytest.approx(0.10)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_signal_observations_round_trip_in_order(kind, tmp_path) -> None:
    factory = await _setup(kind, tmp_path)
    async with factory() as uow:
        await uow.intelligence.signals.upsert_registry(
            [
                {
                    "signal_id": "signal.posting_age",
                    "description": "age",
                    "provider_version": "1.0.0",
                    "default_weight": 0.1,
                }
            ]
        )
        await uow.intelligence.signals.add(
            SignalObservationRecord(
                id="sig_1", job_id="job_1", signal=_signal(), evaluated_at=NOW
            )
        )
        await uow.commit()

    async with factory() as uow:
        rows = await uow.intelligence.signals.list_by_ids(["sig_2", "sig_1"])

    assert len(rows) == 1
    assert rows[0]["id"] == "sig_1"
    assert rows[0]["status"] == "available"
    assert float(rows[0]["risk_contribution"]) == pytest.approx(0.4)
    assert rows[0]["evidence"][0]["kind"] == "check"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_assessments_latest_and_input_hash_lookup(kind, tmp_path) -> None:
    factory = await _setup(kind, tmp_path)
    async with factory() as uow:
        version_id = await uow.intelligence.model_versions.ensure(
            model_id="risk.heuristic_v1",
            model_version="1.0.0",
            config_json="{}",
            config_hash="abc123",
        )
        await uow.intelligence.signals.upsert_registry(
            [
                {
                    "signal_id": "signal.posting_age",
                    "description": "age",
                    "provider_version": "1.0.0",
                    "default_weight": 0.1,
                }
            ]
        )
        for index, (_evaluated_at, risk) in enumerate(
            [
                (datetime(2026, 2, 1, tzinfo=UTC), 0.4),
                (datetime(2026, 3, 1, tzinfo=UTC), 0.7),
            ]
        ):
            await uow.intelligence.assessments.add(
                RiskAssessmentRecord(
                    id=f"ra_{index}",
                    job_id="job_1",
                    assessment=_assessment(risk=risk),
                    model_version_id=version_id,
                    signal_observation_ids=[],
                    input_hash=f"hash_{index}",
                )
            )
        await uow.commit()

    async with factory() as uow:
        latest = await uow.intelligence.assessments.latest("job_1")
        by_hash = await uow.intelligence.assessments.find_by_input_hash(
            "job_1", "hash_0"
        )
        batched = await uow.intelligence.assessments.latest_for_jobs(
            ["job_1", "missing"]
        )
        listed = await uow.intelligence.assessments.list_for_job("job_1")

    assert latest is not None and latest["id"] == "ra_1"
    assert by_hash is not None and by_hash["risk"] == pytest.approx(0.4)
    assert set(batched) == {"job_1"}
    assert [row["id"] for row in listed] == ["ra_1", "ra_0"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_latest_class_counts_use_one_row_per_job(kind, tmp_path) -> None:
    factory = await _setup(kind, tmp_path)
    async with factory() as uow:
        version_id = await uow.intelligence.model_versions.ensure(
            model_id="risk.heuristic_v1",
            model_version="1.0.0",
            config_json="{}",
            config_hash="h1",
        )
        for index, risk in enumerate((0.4, 0.7)):
            await uow.intelligence.assessments.add(
                RiskAssessmentRecord(
                    id=f"ra_{index}",
                    job_id="job_1",
                    assessment=_assessment(risk=risk),
                    model_version_id=version_id,
                    signal_observation_ids=[],
                    input_hash=f"h{index}",
                )
            )
        await uow.commit()

    async with factory() as uow:
        superseded = await uow.intelligence.assessments.count_latest_in_classes(
            ["needs_verification"]
        )
        latest = await uow.intelligence.assessments.count_latest_in_classes(
            ["likely_stale"]
        )
        excluded = await uow.intelligence.assessments.count_latest_in_classes(
            ["likely_stale"], exclude_job_ids=["job_1"]
        )

    assert superseded == 0
    assert latest == 1
    assert excluded == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_model_versions_are_immutable_rows_per_config(kind, tmp_path) -> None:
    factory = await _setup(kind, tmp_path)
    async with factory() as uow:
        first = await uow.intelligence.model_versions.ensure(
            model_id="risk.heuristic_v1",
            model_version="1.0.0",
            config_json='{"a": 1}',
            config_hash="h1",
        )
        again = await uow.intelligence.model_versions.ensure(
            model_id="risk.heuristic_v1",
            model_version="1.0.0",
            config_json='{"a": 1}',
            config_hash="h1",
        )
        second = await uow.intelligence.model_versions.ensure(
            model_id="risk.heuristic_v1",
            model_version="1.0.0",
            config_json='{"a": 2}',
            config_hash="h2",
        )
        await uow.commit()

    async with factory() as uow:
        rows = await uow.intelligence.model_versions.list("risk.heuristic_v1")

    assert first == again
    assert second != first
    assert len(rows) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_company_summaries_and_endpoints(kind, tmp_path) -> None:
    factory = await _setup(kind, tmp_path)
    async with factory() as uow:
        await uow.intelligence.company_summaries.add(
            CompanySummaryRecord(
                id="chs_1",
                company_id="co_1",
                computed_at=NOW,
                sample_tier="limited",
                metrics={"n_unique_postings": 4},
                labels=[],
                evidence=[{"kind": "reposts"}],
            )
        )
        await uow.intelligence.endpoints.add(
            EndpointObservationRecord(
                id="ep_1",
                job_id="job_1",
                observed_at=NOW,
                state="closed_message",
                http_status=200,
                final_url="https://example.com/jobs/1",
                details={"marker": "no longer accepting applications"},
            )
        )
        await uow.commit()

    async with factory() as uow:
        summary = await uow.intelligence.company_summaries.latest("co_1")
        same_day = await uow.intelligence.company_summaries.for_day(
            "co_1", "2026-03-01"
        )
        endpoint = await uow.intelligence.endpoints.latest("job_1")

    assert summary is not None and summary["sample_tier"] == "limited"
    assert summary["metrics"]["n_unique_postings"] == 4
    assert same_day is not None and same_day["id"] == "chs_1"
    assert endpoint is not None
    assert endpoint["state"] == "closed_message"
    assert endpoint["details"]["marker"] == "no longer accepting applications"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["memory", "sqlite"])
async def test_classification_changes_are_listed(kind, tmp_path) -> None:
    factory = await _setup(kind, tmp_path)
    async with factory() as uow:
        version_id = await uow.intelligence.model_versions.ensure(
            model_id="risk.heuristic_v1",
            model_version="1.0.0",
            config_json="{}",
            config_hash="h1",
        )
        score = await uow.intelligence.model_versions.ensure(
            model_id="risk.heuristic_v1",
            model_version="1.0.0",
            config_json="{}",
            config_hash="h1",
        )
        assert score == version_id
        await uow.intelligence.assessments.add(
            RiskAssessmentRecord(
                id="ra_1",
                job_id="job_1",
                assessment=_assessment(),
                model_version_id=version_id,
                signal_observation_ids=[],
                input_hash="h1",
            )
        )
        await uow.intelligence.classification_changes.add(
            ClassificationChangeRecord(
                id="cc_1",
                job_id="job_1",
                from_class="likely_active",
                to_class="likely_stale",
                from_assessment_id=None,
                to_assessment_id="ra_1",
                changed_at=NOW,
            )
        )
        await uow.commit()

    async with factory() as uow:
        rows = await uow.intelligence.classification_changes.list_for_job("job_1")

    assert len(rows) == 1
    assert rows[0]["to_class"] == "likely_stale"


@pytest.mark.asyncio
async def test_sqlite_history_rejects_updates_and_deletes(tmp_path) -> None:
    factory = await _setup("sqlite", tmp_path)
    async with factory() as uow:
        await uow.intelligence.assessments.add(
            RiskAssessmentRecord(
                id="ra_1",
                job_id="job_1",
                assessment=_assessment(),
                model_version_id=await uow.intelligence.model_versions.ensure(
                    model_id="risk.heuristic_v1",
                    model_version="1.0.0",
                    config_json="{}",
                    config_hash="h1",
                ),
                signal_observation_ids=[],
                input_hash="h1",
            )
        )
        await uow.commit()

    db = tmp_path / "intelligence.sqlite"
    async with aiosqlite.connect(db) as conn:
        with pytest.raises(aiosqlite.Error):
            await conn.execute("UPDATE risk_assessments SET risk = 0.1")
        with pytest.raises(aiosqlite.Error):
            await conn.execute("DELETE FROM risk_assessments")

    # The in-memory twin has no mutating method at all.
    memory = InMemoryUnitOfWorkFactory()
    async with memory() as uow:
        assert not hasattr(uow.intelligence.assessments, "update")
        assert not hasattr(uow.intelligence.assessments, "delete")
