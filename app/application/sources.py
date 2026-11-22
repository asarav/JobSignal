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
from dataclasses import replace
from datetime import datetime
from typing import Any, cast

from app.application import suppression as suppression_glue
from app.application.dedup import (
    build_fingerprint,
    record_fingerprint,
    record_repost_event,
    related_candidate,
    resolve_incoming,
)
from app.domain.models.common import Ok, PortError, Provenance
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.extraction import ExtractionTarget, MergedExtraction
from app.domain.models.identity import JobFingerprintSet
from app.domain.models.job import RawJobRef
from app.domain.models.source import SourceConfig
from app.domain.ports.connectors import JobSourceConnector
from app.domain.ports.http import HttpClient
from app.domain.ports.repositories import UnitOfWorkFactory
from app.domain.services.classifier import classify_url
from app.domain.services.fingerprints import simhash64
from app.domain.services.normalizer import (
    company_id_for,
    company_name_for_source,
    content_hash_for,
    normalize_job,
)

DEFAULT_CRAWL_INTERVAL_S = 3600.0

#: Connector used when the classifier cannot recognise an ATS.
FALLBACK_CONNECTOR_ID = "ats.career_site"

#: Provenance strategy recorded on jobs verified from an index-harvested URL.
FUNNEL_STRATEGY_ID = "discovery.funnel"


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


#: How many failing postings keep their details in the run stats.  The counts
#: stay complete; only the samples are bounded, so a board of dead postings
#: cannot turn one crawl into a log flood.
_MAX_ERROR_SAMPLES = 5

#: Fetch failures that settle the *posting*, not the run.  The same URL will
#: answer the same way however often it is asked, so such a failure is no
#: reason to stop the source from retiring the postings it no longer lists.
#: Counting these as run errors was how a single expired posting quietly
#: disabled removal for a whole board.
_SETTLED_FETCH_ERRORS = frozenset({"connector.invalid_response"})


def _note_fetch_error(stats: dict[str, object], error: PortError, url: str) -> None:
    """Record *why* posting fetches failed, not only that some did.

    A crawl used to report ``errors: 40`` and nothing else, which reads the same
    whether forty postings were retired or the host rate-limited the whole run.
    Counting by error code, with the first few full cases, is what makes a
    failing source actionable instead of merely red.
    """
    by_code = stats.setdefault("errors_by_code", {})
    if isinstance(by_code, dict):
        by_code[error.code] = int(by_code.get(error.code, 0)) + 1
    samples = stats.setdefault("error_samples", [])
    if isinstance(samples, list) and len(samples) < _MAX_ERROR_SAMPLES:
        samples.append(
            {
                "code": error.code,
                "retryable": error.retryable,
                "url": url,
                "message": error.message,
            }
        )


def _with_schedule(source: SourceConfig, schedule: dict[str, Any]) -> SourceConfig:
    from dataclasses import replace

    return replace(source, schedule=schedule)


#: How many crawl events one run may write. A board of dead postings should not
#: turn a single crawl into an unbounded event log.
_MAX_EVENTS_PER_RUN = 25


def _source_budget(source: SourceConfig, config: Any) -> tuple[int, float]:
    """Refs and wall-clock time this source may spend (spec §6.6).

    The config carries the defaults; a source may narrow them with
    ``limits: {max_refs, max_duration_s}``.
    """
    crawl = getattr(config, "crawl", None)
    default_refs = int(getattr(crawl, "max_refs_per_source", 500) or 500)
    default_seconds = float(getattr(crawl, "max_duration_s", 300) or 300)
    limits = dict(getattr(source, "limits", {}) or {})
    raw_refs = limits.get("max_refs")
    raw_seconds = limits.get("max_duration_s")
    max_refs = (
        int(raw_refs)
        if isinstance(raw_refs, int) and not isinstance(raw_refs, bool) and raw_refs > 0
        else default_refs
    )
    max_seconds = (
        float(raw_seconds)
        if isinstance(raw_seconds, (int, float))
        and not isinstance(raw_seconds, bool)
        and raw_seconds > 0
        else default_seconds
    )
    return max_refs, max_seconds


async def _record_crawl_event(
    uow_factory: UnitOfWorkFactory,
    run_id: str,
    *,
    code: str,
    message: str,
    now: Any,
    url: str | None = None,
    level: str = "warning",
) -> None:
    """Persist why a crawl stopped or a posting failed (spec §6.6 / §17.4)."""
    try:
        async with uow_factory() as uow:
            await uow.crawl_events.record(
                run_id=run_id,
                at=now,
                level=level,
                code=code,
                message=message,
                url=url,
            )
            await uow.commit()
    except Exception:  # noqa: BLE001 - diagnostics must never fail a crawl
        return


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
    config: Any = None,
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
        #: Postings that answered with a settled "this is gone" page.  They are
        #: counted apart from ``errors`` so they neither flag the run as failed
        #: nor hold back the not-seen rule.
        "gone": 0,
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
    max_refs, max_duration_s = _source_budget(source, config)
    events_recorded = 0

    try:
        refs = await _collect_refs(connector, source, ctx, invoker, source.connector_id)
        refs_total = len(refs)
        if refs_total > max_refs:
            # A truncated listing is partial: it may not prove anything is gone.
            refs = refs[:max_refs]
            stats["refs_total"] = refs_total
            stats["truncated"] = True
        if events is not None:
            events.publish(
                "discovery.progress",
                {"run_id": run_id, "source_id": source.id, "refs_discovered": len(refs)},
                task_id=run_id,
            )
        for ref in refs:
            ctx.cancel.throw_if_cancelled()
            if (clock_now() - started).total_seconds() >= max_duration_s:
                stats["truncated"] = True
                break
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
                    # ``crawl.min_refetch_hours`` is the floor between detail
                    # fetches of an unchanged posting: a hint that still
                    # matches is trustworthy only until it is worth re-checking.
                    skip_fetch = _refetch_allowed(existing, clock_now(), config)
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
                counter = "gone" if result.error.code in _SETTLED_FETCH_ERRORS else "errors"
                stats[counter] = int(str(stats.get(counter, 0))) + 1
                _note_fetch_error(stats, result.error, ref.url)
                if events_recorded < _MAX_EVENTS_PER_RUN:
                    events_recorded += 1
                    await _record_crawl_event(
                        uow_factory,
                        run_id,
                        code=result.error.code,
                        message=result.error.message,
                        now=clock_now(),
                        url=ref.url,
                    )
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
                config=config,
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
    truncated = bool(stats.get("truncated"))
    if truncated:
        await _record_crawl_event(
            uow_factory,
            run_id,
            code="crawl.truncated",
            message="the run stopped at its ref or duration budget",
            now=clock_now(),
        )
    if int(str(stats.get("errors", 0) or 0)) == 0 and not truncated:
        async with uow_factory() as uow:
            threshold, spacing_hours = _not_seen_policy(config)
            removed = await uow.jobs.mark_not_seen(
                source.id,
                seen_ids,
                clock_now(),
                threshold=threshold,
                min_spacing_hours=spacing_hours,
            )
            stats["removed"] = removed
            await uow.crawl_runs.finish(
                run_id, status="completed", finished_at=clock_now(), stats=stats
            )
            await uow.commit()
    else:
        unresolved = int(str(stats.get("errors", 0) or 0))
        if unresolved > 0:
            # A skipped removal that says nothing is how stale postings become
            # invisible: the run reports as finished, nothing is retired, and
            # no record explains it.
            await _record_crawl_event(
                uow_factory,
                run_id,
                code="crawl.removal_skipped",
                message=(
                    f"{unresolved} postings could not be fetched, so postings this "
                    "source no longer lists were left untouched"
                ),
                now=clock_now(),
            )
        # Truncation is a budget stop, not a failed posting: the run did what
        # it was allowed to do. Only real fetch errors make it "with errors".
        status = "completed_with_errors" if unresolved > 0 else "completed"
        async with uow_factory() as uow:
            await uow.crawl_runs.finish(
                run_id, status=status, finished_at=clock_now(), stats=stats
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


async def verify_posting(
    *,
    source: SourceConfig,
    connector: JobSourceConnector,
    url: str,
    source_posting_id: str | None = None,
    uow_factory: UnitOfWorkFactory,
    http: HttpClient,
    clock_now: Any,
    events: Any = None,
    pipeline: Any = None,
    invoker: Any = None,
    cancel: Any = None,
    config: Any = None,
) -> str:
    """Verify one harvested posting URL with a single fetch + extraction pass.

    This is the funnel's cheap path: one connector detail request, the normal
    extraction pipeline and the same ``_persist_job`` used by a full crawl, so
    identity/fingerprint resolution dedupes the posting against any listing
    that already knows it.  It never touches ``mark_not_seen``: a harvested
    URL is not a listing, so it cannot prove that a job disappeared.

    Returns ``new``, ``updated``, ``skipped`` or ``failed``.
    """
    now = clock_now()
    run_id = f"verify_{uuid.uuid4().hex[:12]}"
    ctx = ExecutionContext(
        task_id=run_id,
        clock=_ClockAdapter(clock_now),
        http=http,
        logger=_NullLogger(),
        cancel=cancel if cancel is not None else CancellationToken(),
        deadline=None,
        config={},
    )
    ref = RawJobRef(
        source_id=source.id, url=url, source_posting_id=source_posting_id
    )
    try:
        result = await _call_connector(
            invoker, source.connector_id, connector.fetch_job, ref, source, ctx
        )
        if not isinstance(result, Ok):
            return "failed"
        raw = result.value
        merged = await _extract(raw, source, ctx, pipeline)
        company_name = str(
            merged.value("company_name") or company_name_for_source(source)
        )
        company_id = company_id_for(company_name)
        job = normalize_job(
            merged.raw, source, company_name=company_name, now=now
        )
        content_hash = content_hash_for(job.title, job.description_text)
        provenance = Provenance(
            source_id=source.id,
            strategy_id=FUNNEL_STRATEGY_ID,
            plugin_id=FUNNEL_STRATEGY_ID,
            plugin_version="1.0.0",
            url=url,
            fetched_at=now,
            content_hash=content_hash,
            notes=("via_index",),
        )
        job = replace(job, provenance=(*job.provenance, provenance))
        job_id, outcome = await _persist_job(
            uow_factory=uow_factory,
            job=job,
            company_id=company_id,
            company_name=company_name,
            source=source,
            content_hash=content_hash,
            now=now,
            # This fetch *is* the index path: it must not clear the marker it
            # is about to set elsewhere.
            via_index=True,
            config=config,
        )
    except Exception as exc:  # noqa: BLE001 - one bad posting must not abort the pass
        if events is not None:
            events.publish(
                "discovery.posting_failed",
                {"source_id": source.id, "url": url, "error": str(exc)},
                task_id=run_id,
            )
        return "failed"
    if events is not None:
        events.publish(
            "discovery.posting_verified",
            {
                "source_id": source.id,
                "url": url,
                "job_id": job_id,
                "outcome": outcome,
                "via_index": True,
            },
            task_id=run_id,
        )
    return outcome


def _has_identity_content(job: Any) -> bool:
    """Whether a posting has enough content to compare safely.

    An unreadable page is stored as a partial record keyed by its URL.  Its
    placeholder "Untitled" title and empty description must never be treated as
    a reason to merge it with a different unreadable posting.
    """
    return bool(job.description_text.strip()) or job.title.strip().lower() not in {
        "",
        "untitled",
    }


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
    via_index: bool = False,
    config: Any = None,
) -> tuple[str, str]:
    """Store one normalized job.

    Returns ``(job_id, outcome)`` where outcome is ``new``, ``updated`` or
    ``skipped`` (the posting did not change since the last crawl).

    A posting that is not already known by canonical URL, alias or
    ``(source, source_posting_id)`` goes through identity resolution (§7.3):
    the same job under a new URL/ATS id becomes an alias of the existing job
    plus a ``repost_events`` row, a material change becomes a new snapshot of
    it, and a merely similar posting is linked with a ``similar`` relation.

    When a *full listing* finds the posting unchanged, any "via index" note
    left by the funnel is cleared: the listing is the confirmation that badge
    waits for.  ``via_index`` marks the funnel's own verification pass, which
    must not clear it.
    """
    fingerprint = build_fingerprint(
        job,
        company_id=company_id,
        ats_id=source.connector_id,
        tenant_key=source.ats_tenant_key,
    )
    thresholds, weights, material_delta = _dedup_policy(config)
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
        if job_id is None:
            # An alias recorded by an earlier repost already maps to its job.
            job_id = await uow.aliases.find_by_url(job.canonical_url)

        decision = None
        previous: JobFingerprintSet | None = None
        related_id: str | None = None
        if job_id is None and _has_identity_content(job):
            decision, candidates = await resolve_incoming(
                uow,
                fingerprint,
                thresholds=thresholds,
                weights=weights,
                material_change_delta=material_delta,
            )
            related = related_candidate(decision, candidates)
            if related is not None:
                related_id = related.job_id
                previous = related.fingerprint
            if related is not None and decision.repost_class in (
                "exact_duplicate",
                "reposted_identical",
            ):
                # Same job, new URL/ATS id: attach the alias, record the
                # event, and count the posting as seen without re-storing it.
                job_id = related.job_id
                await uow.aliases.add(
                    job_id=job_id,
                    alias_url=job.canonical_url,
                    alias_source_id=source.id,
                    created_at=now,
                )
                await uow.jobs.touch(job_id, now)
                await uow.observations.add(
                    job_id=job_id,
                    source_id=source.id,
                    observed_at=now,
                    posting_status="active",
                )
                await record_repost_event(
                    uow,
                    job_id=job_id,
                    decision=decision,
                    incoming=fingerprint,
                    previous=previous,
                    now=now,
                )
                await uow.commit()
                return job_id, "skipped"
            if related is not None and decision.repost_class == "materially_updated":
                # Same job, meaningfully changed: keep one job id and append
                # the new snapshot below, with the new URL recorded as alias.
                job_id = related.job_id
                await uow.aliases.add(
                    job_id=job_id,
                    alias_url=job.canonical_url,
                    alias_source_id=source.id,
                    created_at=now,
                )

        is_new = job_id is None
        is_material = True
        if job_id is not None:
            existing = await uow.jobs.get(job_id)
            if existing is not None and existing.get("content_hash") == content_hash:
                if not via_index:
                    await uow.jobs.clear_provenance_note(job_id, "via_index")
                await uow.observations.add(
                    job_id=job_id,
                    source_id=source.id,
                    observed_at=now,
                    posting_status="active",
                )
                await uow.commit()
                return job_id, "skipped"
            if existing is not None:
                is_material = _is_material_change(
                    existing, job, previous, fingerprint, material_delta
                )
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
        await record_fingerprint(uow, job_id=job_id, fingerprint=fingerprint, now=now)
        # A new or materially updated posting is checked against the user's
        # applications straight away, so it never flashes into the results.
        await suppression_glue.evaluate_job(
            uow,
            job_id,
            policy=await suppression_glue.load_policy(uow, config),
            now=now,
        )
        if decision is not None:
            await record_repost_event(
                uow,
                job_id=job_id,
                decision=decision,
                incoming=fingerprint,
                previous=previous,
                now=now,
            )
            if decision.repost_class == "similar_distinct" and related_id is not None:
                # Similar but distinct: never merged, only linked for review.
                await uow.jobs.add_relation(
                    job_id,
                    related_id,
                    "similar",
                    confidence=float(decision.confidence),
                    evidence=list(decision.evidence),
                    created_by="auto",
                )
        await uow.commit()
        return job_id, ("new" if is_new else "updated")


def _dedup_policy(
    config: Any,
) -> tuple[dict[str, float] | None, dict[str, float] | None, float]:
    """Dedup thresholds, weights and material delta from the typed config."""
    dedup = getattr(config, "dedup", None)
    if dedup is None:
        return None, None, 0.10
    thresholds = getattr(dedup, "thresholds", None)
    threshold_map = {
        key: float(getattr(thresholds, key))
        for key in ("reposted_identical", "materially_updated", "similar_distinct")
        if thresholds is not None and hasattr(thresholds, key)
    }
    weights = getattr(dedup, "weights", None)
    weight_map = (
        {str(key): float(value) for key, value in weights.items()}
        if isinstance(weights, dict)
        else None
    )
    delta = float(getattr(dedup, "material_change_jaccard_delta", 0.10))
    return threshold_map or None, weight_map, delta


#: Minimum spacing between the not-seen observations that count toward removal
#: (two successful listings must be at least this far apart).
NOT_SEEN_MIN_SPACING_HOURS = 24


def _not_seen_policy(config: Any) -> tuple[int, int]:
    """``crawl.not_seen_threshold`` and the fixed not-seen spacing."""
    crawl = getattr(config, "crawl", None)
    threshold = int(getattr(crawl, "not_seen_threshold", 2) or 2)
    return max(1, threshold), NOT_SEEN_MIN_SPACING_HOURS


def _refetch_allowed(existing: Any, now: datetime, config: Any) -> bool:
    """Whether an unchanged hint may be trusted without another fetch.

    A matching ``updated_at_hint`` means the ATS says nothing changed; the
    posting is still re-fetched once the last fetch is older than
    ``crawl.min_refetch_hours``, because some boards never bump the hint.
    """
    if not isinstance(existing, dict):
        return False
    fetched_at = existing.get("fetched_at") or existing.get("last_seen_at")
    if not isinstance(fetched_at, str) or not fetched_at:
        return True
    crawl = getattr(config, "crawl", None)
    min_hours = int(getattr(crawl, "min_refetch_hours", 6) or 6)
    try:
        fetched = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=now.tzinfo)
    elapsed_hours = (now - fetched).total_seconds() / 3600.0
    return elapsed_hours >= max(1, min_hours)


def _is_material_change(
    existing: Any,
    job: Any,
    previous: JobFingerprintSet | None,
    incoming: JobFingerprintSet,
    delta: float,
) -> bool:
    """Material-change assessment for an update (spec §7.3)."""
    if previous is not None:
        from app.domain.services.identity import is_material_change

        return is_material_change(incoming, previous, delta)
    if existing.get("title") != job.title:
        return True
    if existing.get("seniority") != job.seniority.value:
        return True
    if existing.get("workplace_type") != job.workplace_type.value:
        return True
    if existing.get("department") != job.department:
        return True
    old_description = str(existing.get("description_text") or "")
    if old_description:
        similarity = 1.0 - (
            (simhash64(old_description) ^ (incoming.desc_simhash64 or 0)).bit_count()
            / 64.0
        )
        if (1.0 - similarity) >= delta:
            return True
    return False


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
