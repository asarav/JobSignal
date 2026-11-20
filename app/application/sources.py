"""Source registry and crawl orchestration (M1).

This module owns the "run one source" flow:

1. list the postings the connector knows about,
2. normalize each posting into the canonical job model,
3. persist the job (skipping unchanged postings),
4. append a snapshot when the content actually changed,
5. record an observation for every posting seen,
6. mark postings that disappeared as removed.

Failures are isolated per source: a broken board never stops the other
sources, and the crawl run is recorded as ``failed`` with the reason.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, cast

from app.domain.models.common import Ok
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.extraction import ExtractionTarget, MergedExtraction
from app.domain.models.source import SourceConfig
from app.domain.ports.connectors import JobSourceConnector
from app.domain.ports.http import HttpClient
from app.domain.ports.repositories import UnitOfWorkFactory
from app.domain.services.classifier import classify_url
from app.domain.services.normalizer import (
    company_id_for,
    company_name_for_source,
    content_hash_for,
    normalize_job,
)

DEFAULT_CRAWL_INTERVAL_S = 3600.0

#: Connector used when the classifier cannot recognise an ATS.
FALLBACK_CONNECTOR_ID = "ats.career_site"


class SourceRegistry:
    """Stores sources and maps them to the connector that can read them."""

    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        connectors: Sequence[JobSourceConnector],
        invoker: Any = None,
    ) -> None:
        self._uow_factory = uow_factory
        self._connectors = {c.id: c for c in connectors}
        #: Optional isolation shim used to call connectors safely.
        self._invoker = invoker

    @property
    def invoker(self) -> Any:
        return self._invoker

    def connector_for(self, connector_id: str | None) -> JobSourceConnector | None:
        """The connector for an id, or the generic career-site fallback."""
        if connector_id is None:
            return self._connectors.get(FALLBACK_CONNECTOR_ID)
        return self._connectors.get(connector_id)

    def classify(self, url: str) -> str | None:
        connector_id, _ = classify_url(url)
        return connector_id

    async def add(
        self,
        url: str,
        *,
        connector_id: str | None = None,
        company_id: str | None = None,
        interval_s: float = DEFAULT_CRAWL_INTERVAL_S,
        tenant_key: str | None = None,
    ) -> SourceConfig:
        if connector_id is None:
            # Unknown ATS: fall back to the generic career-site connector.
            connector_id = self.classify(url) or FALLBACK_CONNECTOR_ID
        source = SourceConfig(
            id=f"src_{uuid.uuid4().hex[:12]}",
            url=url,
            kind="ats" if connector_id != FALLBACK_CONNECTOR_ID else "career_site",
            company_id=company_id,
            connector_id=connector_id,
            ats_tenant_key=tenant_key,
            schedule={"interval_s": interval_s},
        )
        async with self._uow_factory() as uow:
            await uow.sources.add(source)
            await uow.commit()
        return source

    async def list_sources(self, *, enabled_only: bool = False) -> Sequence[SourceConfig]:
        async with self._uow_factory() as uow:
            return await uow.sources.list(enabled_only=enabled_only)

    async def get(self, source_id: str) -> SourceConfig | None:
        async with self._uow_factory() as uow:
            return await uow.sources.get(source_id)

    async def set_enabled(self, source_id: str, enabled: bool) -> None:
        async with self._uow_factory() as uow:
            await uow.sources.set_enabled(source_id, enabled)
            await uow.commit()

    async def set_extraction_order(self, source_id: str, order: list[str]) -> None:
        async with self._uow_factory() as uow:
            source = await uow.sources.get(source_id)
            if source is None:
                return
            from dataclasses import replace

            await uow.sources.save(replace(source, extraction_order=tuple(order)))
            await uow.commit()

    async def set_schedule(self, source_id: str, interval_s: float) -> None:
        """Store how often the source should be crawled."""
        async with self._uow_factory() as uow:
            source = await uow.sources.get(source_id)
            if source is None:
                return
            await uow.sources.save(
                _with_schedule(source, {"interval_s": interval_s}),
            )
            await uow.commit()

    async def delete(self, source_id: str) -> None:
        async with self._uow_factory() as uow:
            await uow.sources.delete(source_id)
            await uow.commit()


def _with_schedule(source: SourceConfig, schedule: dict[str, Any]) -> SourceConfig:
    from dataclasses import replace

    return replace(source, schedule=schedule)


async def run_crawl(
    *,
    source: SourceConfig,
    connector: JobSourceConnector,
    uow_factory: UnitOfWorkFactory,
    http: HttpClient,
    clock_now: Any,
    events: Any = None,
    pipeline: Any = None,
    invoker: Any = None,
    cancel: Any = None,
) -> dict[str, object]:
    """Run one source crawl and return its stats."""
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    started = clock_now()
    stats: dict[str, object] = {
        "discovered": 0,
        "new": 0,
        "updated": 0,
        "skipped": 0,
        "removed": 0,
        "errors": 0,
    }

    async with uow_factory() as uow:
        await uow.crawl_runs.create(run_id, source.id, started)
        await uow.commit()

    if events is not None:
        events.publish("crawl.started", {"run_id": run_id, "source_id": source.id}, task_id=run_id)

    ctx = ExecutionContext(
        task_id=run_id,
        clock=_ClockAdapter(clock_now),
        http=http,
        logger=_NullLogger(),
        cancel=cancel if cancel is not None else CancellationToken(),
        deadline=None,
        config={},
    )

    default_company = company_name_for_source(source)
    seen_ids: list[str] = []

    try:
        refs = await _collect_refs(connector, source, ctx, invoker, source.connector_id)
        if events is not None:
            events.publish(
                "discovery.progress",
                {"run_id": run_id, "source_id": source.id, "refs_discovered": len(refs)},
                task_id=run_id,
            )
        for ref in refs:
            ctx.cancel.throw_if_cancelled()
            stats["discovered"] = int(str(stats.get("discovered", 0))) + 1
            # Incremental fetch skip: if the ATS says the posting's
            # updated_at hasn't changed since last crawl, reuse the stored
            # record without re-fetching.
            existing_id = None
            if getattr(ref, "source_posting_id", None):
                async with uow_factory() as _uow:
                    existing_id = await _uow.jobs.find_by_source_posting(
                        source.id, ref.source_posting_id
                    )
            skip_fetch = False
            if existing_id is not None and getattr(ref, "updated_at_hint", None):
                async with uow_factory() as _uow:
                    existing = await _uow.jobs.get(existing_id)
                if existing is not None and existing.get("updated_at_hint") == ref.updated_at_hint:
                    skip_fetch = True
            if skip_fetch and existing_id is not None:
                async with uow_factory() as _uow:
                    await _uow.observations.add(
                        job_id=existing_id,
                        source_id=source.id,
                        observed_at=clock_now(),
                        posting_status="active",
                    )
                    await _uow.commit()
                stats["skipped"] = int(str(stats.get("skipped", 0))) + 1
                seen_ids.append(existing_id)
                continue

            result = await _call_connector(
                invoker,
                source.connector_id,
                connector.fetch_job,
                ref,
                source,
                ctx,
            )
            if not isinstance(result, Ok):
                stats["errors"] = int(str(stats["errors"])) + 1
                continue

            raw = result.value
            merged = await _extract(raw, source, ctx, pipeline)
            # The page may name the company more precisely than the board URL.
            company_name = str(merged.value("company_name") or default_company)
            company_id = company_id_for(company_name)
            job = normalize_job(merged.raw, source, company_name=company_name, now=clock_now())
            content_hash = content_hash_for(job.title, job.description_text)
            job_id, outcome = await _persist_job(
                uow_factory=uow_factory,
                job=job,
                company_id=company_id,
                company_name=company_name,
                source=source,
                content_hash=content_hash,
                now=clock_now(),
                updated_at_hint=getattr(ref, "updated_at_hint", None),
            )
            stats[outcome] = int(str(stats[outcome])) + 1
            seen_ids.append(job_id)
    except Exception as exc:  # noqa: BLE001 - isolate the failing source
        async with uow_factory() as uow:
            await uow.crawl_runs.finish(
                run_id,
                status="failed",
                finished_at=clock_now(),
                stats=stats,
                error=str(exc),
            )
            await uow.commit()
        stats["error"] = str(exc)
        if events is not None:
            events.publish(
                "crawl.failed",
                {"run_id": run_id, "source_id": source.id, "error": str(exc)},
                task_id=run_id,
            )
        return stats

    # Postings that the source no longer lists are marked removed, but only
    # when the listing was fully successful (partial/failed listings never
    # create not_seen observations).
    if int(str(stats.get("errors", 0) or 0)) == 0:
        async with uow_factory() as uow:
            removed = await uow.jobs.mark_not_seen(source.id, seen_ids, clock_now())
            stats["removed"] = removed
            await uow.crawl_runs.finish(
                run_id, status="completed", finished_at=clock_now(), stats=stats
            )
            await uow.commit()
    else:
        async with uow_factory() as uow:
            await uow.crawl_runs.finish(
                run_id, status="completed_with_errors", finished_at=clock_now(), stats=stats
            )
            await uow.commit()
    if events is not None:
        events.publish(
            "crawl.completed",
            {"run_id": run_id, "source_id": source.id, "stats": stats},
            task_id=run_id,
        )
    if events is not None:
        events.publish(
            "discovery.source_completed",
            {"run_id": run_id, "source_id": source.id, "stats": stats},
            task_id=run_id,
        )
    return stats


async def _persist_job(
    *,
    uow_factory: UnitOfWorkFactory,
    job: Any,
    company_id: str,
    company_name: str,
    source: SourceConfig,
    content_hash: str,
    now: datetime,
    updated_at_hint: str | None = None,
) -> tuple[str, str]:
    """Store one normalized job.

    Returns ``(job_id, outcome)`` where outcome is ``new``, ``updated`` or
    ``skipped`` (the posting did not change since the last crawl).
    """
    async with uow_factory() as uow:
        await uow.companies.upsert(company_id, company_name, normalized_name=company_name.lower())
        job_id = await uow.jobs.find_by_canonical_url(job.canonical_url)
        if job_id is None and job.source_posting_id:
            # Same posting seen under a new URL: keep one job, record the alias.
            match = await uow.jobs.find_by_source_posting(source.id, job.source_posting_id)
            if match is not None:
                job_id = match
                await uow.aliases.add(
                    job_id=match,
                    alias_url=job.canonical_url,
                    alias_source_id=source.id,
                    created_at=now,
                )
        is_new = job_id is None
        is_material = True
        if job_id is not None:
            existing = await uow.jobs.get(job_id)
            if existing is not None and existing.get("content_hash") == content_hash:
                await uow.observations.add(
                    job_id=job_id,
                    source_id=source.id,
                    observed_at=now,
                    posting_status="active",
                )
                await uow.commit()
                return job_id, "skipped"
            # material change assessment
            if existing is not None:
                is_material = False
                if existing.get("title") != job.title:
                    is_material = True
                elif existing.get("seniority") != job.seniority.value:
                    is_material = True
                elif existing.get("workplace_type") != job.workplace_type.value:
                    is_material = True
        else:
            job_id = f"job_{uuid.uuid4().hex[:12]}"

        await uow.jobs.upsert(
            job_id,
            company_id=company_id,
            source_id=source.id,
            job=job,
            content_hash=content_hash,
            updated_at_hint=updated_at_hint,
        )
        await uow.snapshots.add(
            job_id=job_id,
            content_hash=content_hash,
            snapshot={
                "title": job.title,
                "description_text": job.description_text,
                "canonical_url": job.canonical_url,
                "employment_type": job.employment_type.value,
                "seniority": job.seniority.value,
                "workplace_type": job.workplace_type.value,
                "is_material_change": is_material,
            },
            created_at=now,
        )
        await uow.observations.add(
            job_id=job_id,
            source_id=source.id,
            observed_at=now,
            posting_status="active",
        )
        await uow.commit()
        return job_id, ("new" if is_new else "updated")


async def _collect_refs(
    connector: JobSourceConnector,
    source: SourceConfig,
    ctx: ExecutionContext,
    invoker: Any,
    connector_id: str | None,
) -> list[Any]:
    """List the postings of a source, through the isolation shim when possible.

    The listing is materialized first so the shim's timeout covers the whole
    operation, not only the creation of the iterator.
    """

    async def collect() -> list[Any]:
        refs: list[Any] = []
        async for ref in await connector.list_jobs(source, ctx):
            ctx.cancel.throw_if_cancelled()
            refs.append(ref)
        return refs

    return list(await _call_connector(invoker, connector_id, collect))


async def _call_connector(
    invoker: Any,
    connector_id: str | None,
    fn: Any,
    *args: Any,
) -> Any:
    """Call a connector directly, or through the plugin isolation shim."""
    if invoker is None or connector_id is None:
        return await fn(*args)
    return await invoker.invoke(connector_id, fn, *args)


async def _extract(
    raw: Any,
    source: SourceConfig,
    ctx: ExecutionContext,
    pipeline: Any,
) -> MergedExtraction:
    """Run the extraction pipeline over the material a connector returned.

    Without a pipeline the raw posting is used as-is, which keeps the crawler
    usable in tests and in headless setups.
    """
    if pipeline is None:
        return MergedExtraction(raw=raw, fields={}, strategies_used=())
    target = ExtractionTarget(
        url=raw.ref.url,
        source_id=source.id,
        html=raw.body_html,
        json_payload=dict(raw.payload) if raw.payload else None,
    )
    merged = await pipeline.extract(target, ctx, order=source.extraction_order)
    return cast(MergedExtraction, merged)


class _ClockAdapter:
    """Adapt a ``now()`` callable to the ``Clock`` protocol (M1 helper)."""

    def __init__(self, now_fn: Any) -> None:
        self._now = now_fn

    def now(self) -> Any:
        return self._now()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class _NullLogger:
    """Logger that swallows records (the crawl runner logs via events)."""

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
