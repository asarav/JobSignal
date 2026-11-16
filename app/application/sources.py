"""Source registry and crawl orchestration helpers (M1)."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.job import NormalizedJob
from app.domain.models.source import SourceConfig
from app.domain.ports.connectors import JobSourceConnector
from app.domain.ports.repositories import UnitOfWorkFactory
from app.domain.services.classifier import classify_url
from app.infrastructure.http.policy_client import PolicyHttpClient


class SourceRegistry:
    def __init__(self, uow_factory: UnitOfWorkFactory, connectors: Sequence[JobSourceConnector]) -> None:
        self._uow_factory = uow_factory
        self._connectors = {c.id: c for c in connectors}

    def connector_for(self, connector_id: str | None) -> JobSourceConnector | None:
        if connector_id is None:
            return None
        return self._connectors.get(connector_id)

    def classify(self, url: str) -> str | None:
        connector_id, _ = classify_url(url)
        return connector_id

    async def add(self, url: str, *, connector_id: str | None = None, company_id: str | None = None) -> SourceConfig:
        if connector_id is None:
            connector_id = self.classify(url)
        source = SourceConfig(
            id=f"src_{uuid.uuid4().hex[:12]}",
            url=url,
            kind="ats" if connector_id else "career_site",
            company_id=company_id,
            connector_id=connector_id,
        )
        async with self._uow_factory() as uow:
            await uow.sources.add(source)
            await uow.commit()
        return source

    async def list(self, *, enabled_only: bool = False) -> Sequence[SourceConfig]:
        async with self._uow_factory() as uow:
            return await uow.sources.list(enabled_only=enabled_only)

    async def set_enabled(self, source_id: str, enabled: bool) -> None:
        async with self._uow_factory() as uow:
            await uow.sources.set_enabled(source_id, enabled)
            await uow.commit()

    async def delete(self, source_id: str) -> None:
        async with self._uow_factory() as uow:
            await uow.sources.delete(source_id)
            await uow.commit()


async def run_crawl(
    *,
    source: SourceConfig,
    connector: JobSourceConnector,
    uow_factory: UnitOfWorkFactory,
    http: PolicyHttpClient,
    clock_now: Any,
) -> dict[str, object]:
    """Run one source crawl. Returns stats. Isolates failures per source."""


    run_id = f"run_{uuid.uuid4().hex[:12]}"
    started = clock_now()
    stats: dict[str, object] = {"discovered": 0, "new": 0, "updated": 0, "skipped": 0, "removed": 0, "errors": 0}
    async with uow_factory() as uow:
        await uow.crawl_runs.create(run_id, source.id, started)
        await uow.commit()

    ctx = ExecutionContext(
        task_id=run_id,
        clock=_ClockAdapter(clock_now),
        http=http,
        logger=_NullLogger(),
        cancel=CancellationToken(),
        deadline=None,
        config={},
    )

    seen_ids: list[str] = []
    try:
        async for ref in await connector.list_jobs(source, ctx):
            stats["discovered"] = int(str(stats["discovered"])) + 1
            result = await connector.fetch_job(ref, ctx)
            from app.domain.models.common import Ok

            if not isinstance(result, Ok):
                stats["errors"] = int(str(stats["errors"])) + 1
                continue
            raw = result.value
            # Normalize via a minimal normalizer v1
            normalized = _normalize(raw, source)
            import hashlib

            content_hash = hashlib.sha256(
                ((normalized.title or "") + "\n" + (normalized.description_text or "")).encode()
            ).hexdigest()
            job_id = f"job_{uuid.uuid4().hex[:12]}"
            async with uow_factory() as uow:
                company_id = f"co_{uuid.uuid4().hex[:8]}"
                await uow.companies.upsert(company_id, normalized.company_ref.name if hasattr(normalized, "company_ref") else (raw.title or "Unknown"))
                existing = await uow.jobs.find_by_canonical_url(normalized.canonical_url)
                if existing is not None:
                    existing_row = await uow.jobs.get(existing)
                    if existing_row is not None and existing_row.get("content_hash") == content_hash:
                        # Unchanged: just record observation and skip.
                        await uow.observations.add(
                            job_id=existing, source_id=source.id, observed_at=clock_now(), posting_status="active"
                        )
                        await uow.commit()
                        seen_ids.append(existing)
                        stats["skipped"] = int(str(stats["skipped"])) + 1
                        continue
                if existing is None:
                    await uow.jobs.upsert(job_id, company_id=company_id, source_id=source.id, job=normalized, content_hash=content_hash)
                    stats["new"] = int(str(stats["new"])) + 1
                else:
                    await uow.jobs.upsert(existing, company_id=company_id, source_id=source.id, job=normalized, content_hash=content_hash)
                    stats["updated"] = int(str(stats["updated"])) + 1
                await uow.observations.add(job_id=existing or job_id, source_id=source.id, observed_at=clock_now(), posting_status="active")
                await uow.commit()
            seen_ids.append(existing or job_id)
    except Exception as exc:  # isolate failing source
        async with uow_factory() as uow:
            await uow.crawl_runs.finish(
                run_id, status="failed", finished_at=clock_now(), stats=stats, error=str(exc)
            )
            await uow.commit()
        stats["error"] = str(exc)
        return stats

    # Not-seen removal logic
    removed = 0
    async with uow_factory() as uow:
        removed = await uow.jobs.mark_not_seen(source.id, seen_ids, clock_now())
        stats["removed"] = removed
        await uow.crawl_runs.finish(run_id, status="completed", finished_at=clock_now(), stats=stats)
        await uow.commit()
    return stats


def _normalize(raw: Any, source: SourceConfig) -> NormalizedJob:

    from app.domain.models.company import CompanyRef
    from app.domain.models.enums import (
        EmploymentType,
        ExtractionStatus,
        PostingStatus,
        Seniority,
        WorkplaceType,
    )
    from app.domain.models.job import NormalizedJob

    title = raw.title or "Untitled"
    company_name = "Unknown"
    # Try to derive company from source url tenant
    locations: list[Any] = []
    return NormalizedJob(
        company_ref=CompanyRef(name=company_name),
        title=title,
        normalized_title=title.lower().strip(),
        title_family=None,
        description_text=raw.body_text or "",
        description_html=raw.body_html,
        locations=locations,
        workplace_type=WorkplaceType.UNKNOWN,
        employment_type=EmploymentType.UNKNOWN,
        salary=None,
        posted_at=None,
        source_id=source.id,
        source_posting_id=raw.ref.source_posting_id,
        canonical_url=raw.ref.url,
        original_urls=[raw.ref.url],
        department=None,
        seniority=Seniority.UNKNOWN,
        required_skills=[],
        preferred_skills=[],
        extraction_status=ExtractionStatus.COMPLETE if raw.body_text else ExtractionStatus.PARTIAL,
        extraction_confidence=0.5 if raw.body_text else 0.3,
        field_confidence={},
        provenance=[],
        posting_status_hint=PostingStatus.ACTIVE,
        fetched_at=datetime.now(UTC),
    )


class _ClockAdapter:
    """Adapt a ``now()`` callable to the ``Clock`` protocol (M1 helper)."""

    def __init__(self, now_fn: Any) -> None:
        self._now = now_fn

    def now(self) -> Any:
        return self._now()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class _NullLogger:
    def bind(self, **fields: Any) -> _NullLogger:
        return self

    def debug(self, event: str, **fields: Any) -> None:
        pass

    def info(self, event: str, **fields: Any) -> None:
        pass

    def warning(self, event: str, **fields: Any) -> None:
        pass

    def error(self, event: str, **fields: Any) -> None:
        pass
