"""Job listing/search/history use cases (M3)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, cast

from app.application.container import Container
from app.application.dto import (
    JobDetailResult,
    JobFilter,
    JobListResult,
    JobSearchParams,
    JobSummary,
)
from app.domain.models.common import PortError


class JobsUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container

    async def list(self, params: JobFilter) -> JobListResult:
        if not params.filters:
            async with self._container.uow_factory() as uow:
                if params.query:
                    rows = await uow.jobs.search(
                        params.query, limit=params.limit, offset=params.offset
                    )
                else:
                    rows = await uow.jobs.list(
                        source_id=params.source_id,
                        company_id=params.company_id,
                        limit=params.limit,
                        offset=params.offset,
                    )
                total = await uow.jobs.count()
                await uow.commit()
            return JobListResult(jobs=[_summary(r) for r in rows], total=total)

        async with self._container.uow_factory() as uow:
            rows, total = await uow.jobs.filtered_page(
                params.filters,
                query=params.query,
                source_id=params.source_id,
                company_id=params.company_id,
                limit=params.limit,
                offset=params.offset,
            )
            await uow.commit()
        return JobListResult(jobs=[_summary(r) for r in rows], total=total)

    async def search(self, params: JobSearchParams) -> JobListResult:
        async with self._container.uow_factory() as uow:
            rows = await uow.jobs.search(params.query, limit=params.limit)
            await uow.commit()
        return JobListResult(jobs=[_summary(r) for r in rows], total=len(rows))

    async def get_detail(self, job_id: str) -> JobDetailResult:
        """Full job record with the history the /jobs/:id view needs."""
        async with self._container.uow_factory() as uow:
            row = await uow.jobs.get(job_id)
            if row is None:
                raise PortError(
                    code="not_found", message=f"unknown job: {job_id}", retryable=False
                )
            snapshots = await uow.snapshots.list_for_job(job_id)
            observations = await uow.observations.list_for_job(job_id)
            relations = await uow.jobs.list_relations(job_id)
            reposts = await uow.dedup.list_repost_events(job_id)
            await uow.commit()
        return JobDetailResult(
            job=_jsonable(row),
            snapshots=[_jsonable(item) for item in snapshots],
            observations=[_jsonable(item) for item in observations],
            relations=[_jsonable(item) for item in relations],
            reposts=[_jsonable(item) for item in reposts],
        )

    async def merge(self, job_id: str, related_job_id: str) -> None:
        """Merge a duplicate into a primary job (manual override, §7.3).

        The duplicate's URL becomes an alias of the primary so later crawls of
        that URL resolve to it, and a ``user_merged`` relation records the
        decision.  A previous ``user_unmerged`` for the same pair is cleared:
        the user's newest manual decision wins.
        """
        if job_id == related_job_id:
            raise PortError(
                code="validation.invalid_params",
                message="a job cannot be merged with itself",
                retryable=False,
            )
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            primary = await uow.jobs.get(job_id)
            duplicate = await uow.jobs.get(related_job_id)
            if primary is None or duplicate is None:
                missing = job_id if primary is None else related_job_id
                raise PortError(
                    code="not_found", message=f"unknown job: {missing}", retryable=False
                )
            await uow.jobs.remove_relation(job_id, related_job_id, "user_unmerged")
            relations = await uow.jobs.list_relations(job_id)
            already = any(
                r.get("relation") == "user_merged"
                and str(r.get("related_job_id")) == related_job_id
                for r in relations
            )
            if not already:
                await uow.jobs.add_relation(
                    job_id,
                    related_job_id,
                    "user_merged",
                    confidence=1.0,
                    created_by="user",
                )
            alias_url = duplicate.get("canonical_url")
            if isinstance(alias_url, str) and alias_url:
                await uow.aliases.add(
                    job_id=job_id,
                    alias_url=alias_url,
                    alias_source_id=cast(str | None, duplicate.get("source_id")),
                    created_at=now,
                )
            await uow.commit()

    async def unmerge(self, job_id: str, related_job_id: str) -> None:
        """Undo a merge: drop the relation and the alias, record the override."""
        async with self._container.uow_factory() as uow:
            primary = await uow.jobs.get(job_id)
            duplicate = await uow.jobs.get(related_job_id)
            if primary is None or duplicate is None:
                missing = job_id if primary is None else related_job_id
                raise PortError(
                    code="not_found", message=f"unknown job: {missing}", retryable=False
                )
            removed = await uow.jobs.remove_relation(
                job_id, related_job_id, "user_merged"
            )
            alias_url = duplicate.get("canonical_url")
            if isinstance(alias_url, str) and alias_url:
                await uow.aliases.remove(job_id, alias_url)
            if removed:
                # Automation must never re-merge what the user split apart.
                await uow.jobs.add_relation(
                    job_id,
                    related_job_id,
                    "user_unmerged",
                    confidence=1.0,
                    created_by="user",
                )
            await uow.commit()

    async def list_duplicates(self) -> JobListResult:
        """Jobs linked to another job, with the ids needed to unmerge."""
        async with self._container.uow_factory() as uow:
            ids = list(await uow.jobs.relation_job_ids())
            rows = await uow.jobs.list_by_ids(ids)
            relations: dict[str, list[dict[str, object]]] = {}
            for job_id in ids:
                relations[job_id] = [
                    dict(item) for item in await uow.jobs.list_relations(job_id)
                ]
            await uow.commit()
        jobs: list[JobSummary] = []
        for row in rows:
            job_id = str(row.get("id"))
            related = _related_ids(relations.get(job_id, []), job_id)
            jobs.append(_summary(row, related_job_ids=related))
        return JobListResult(jobs=jobs, total=len(jobs))

    async def list_seen(self) -> JobListResult:
        """Previously seen / removed / duplicate jobs (spec §8.3 / §16.2)."""
        async with self._container.uow_factory() as uow:
            rows = await uow.jobs.list(limit=200)
            duplicate_ids = set(await uow.jobs.relation_job_ids())
            relations: dict[str, list[dict[str, object]]] = {}
            for row in rows:
                job_id = str(row.get("id"))
                if job_id in duplicate_ids:
                    relations[job_id] = [
                        dict(item) for item in await uow.jobs.list_relations(job_id)
                    ]
            await uow.commit()
        seen: list[JobSummary] = []
        for row in rows:
            job_id = str(row.get("id"))
            status = str(row.get("posting_status") or "active")
            if job_id not in duplicate_ids and status == "active":
                continue
            related = _related_ids(relations.get(job_id, []), job_id)
            reason = "duplicate" if job_id in duplicate_ids else f"posting_{status}"
            seen.append(
                _summary(row, reason=reason, related_job_ids=related)
            )
        return JobListResult(jobs=seen, total=len(seen))


def _summary(
    row: Mapping[str, Any],
    *,
    reason: str | None = None,
    related_job_ids: Sequence[str] = (),
) -> JobSummary:
    return JobSummary(
        job_id=str(row.get("id")),
        title=str(row.get("title") or ""),
        company_id=cast(str, row.get("company_id"))
        if row.get("company_id") is not None
        else None,
        canonical_url=cast(str, row.get("canonical_url"))
        if row.get("canonical_url") is not None
        else None,
        posting_status=str(row.get("posting_status") or "active"),
        source_id=cast(str, row.get("source_id"))
        if row.get("source_id") is not None
        else None,
        provenance=_provenance_strategies(row),
        via_index=_via_index(row),
        reason=reason,
        related_job_ids=list(related_job_ids),
    )


def _related_ids(
    relations: Sequence[Mapping[str, Any]], job_id: str
) -> list[str]:
    out: list[str] = []
    for relation in relations:
        other = (
            relation.get("related_job_id")
            if str(relation.get("job_id")) == job_id
            else relation.get("job_id")
        )
        if isinstance(other, str) and other != job_id and other not in out:
            out.append(other)
    return out


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _via_index(row: Mapping[str, Any]) -> bool:
    """Whether a posting was found by the index funnel, not a board listing."""
    entries = _provenance_entries(row)
    for entry in entries:
        notes = entry.get("notes")
        if isinstance(notes, (list, tuple)) and "via_index" in notes:
            return True
    return False


def _provenance_entries(row: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = row.get("provenance")
    if isinstance(raw, str):
        try:
            import json

            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [entry for entry in raw if isinstance(entry, Mapping)]


def _provenance_strategies(row: Mapping[str, Any]) -> list[str]:
    """The strategies that contributed to a job, in pipeline order."""
    seen: list[str] = []
    for entry in _provenance_entries(row):
        strategy = entry.get("strategy_id")
        if isinstance(strategy, str) and strategy not in seen:
            seen.append(strategy)
    return seen
