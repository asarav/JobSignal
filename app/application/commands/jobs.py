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
    RankedJobListResult,
    RankedJobParams,
)
from app.domain.models.common import PortError
from app.domain.models.enums import RiskClass
from app.domain.services.company_resolution import canonical_map
from app.domain.services.ranking import (
    DEFAULT_POLICY,
    explain,
    rank_score,
    visibility,
)
from app.domain.services.risk import CLASS_LABELS


class JobsUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container

    async def list(self, params: JobFilter) -> JobListResult:
        """Default result lists never show suppressed or dismissed jobs.

        Suppression removes a job from the ordinary views but keeps it in the
        Seen view and searchable history (spec §8.2 rule 9).
        """
        if not params.filters:
            async with self._container.uow_factory() as uow:
                excluded = await _hidden_job_ids(uow, self._container.clock.now())
                if params.query:
                    rows = await uow.jobs.search(
                        params.query,
                        limit=params.limit,
                        offset=params.offset,
                        exclude_job_ids=excluded,
                    )
                else:
                    rows = await uow.jobs.list(
                        source_id=params.source_id,
                        company_id=params.company_id,
                        limit=params.limit,
                        offset=params.offset,
                        exclude_job_ids=excluded,
                    )
                total = await uow.jobs.count(exclude_job_ids=excluded)
                summaries = [_summary(r) for r in rows]
                await _enrich(uow, summaries)
                await uow.commit()
            return JobListResult(jobs=summaries, total=total)

        async with self._container.uow_factory() as uow:
            excluded = await _hidden_job_ids(uow, self._container.clock.now())
            rows, total = await uow.jobs.filtered_page(
                params.filters,
                query=params.query,
                source_id=params.source_id,
                company_id=params.company_id,
                limit=params.limit,
                offset=params.offset,
                exclude_job_ids=excluded,
            )
            summaries = [_summary(r) for r in rows]
            await _enrich(uow, summaries)
            await uow.commit()
        return JobListResult(jobs=summaries, total=total)

    async def search(self, params: JobSearchParams) -> JobListResult:
        async with self._container.uow_factory() as uow:
            excluded = await _hidden_job_ids(uow, self._container.clock.now())
            rows = await uow.jobs.search(
                params.query, limit=params.limit, exclude_job_ids=excluded
            )
            summaries = [_summary(r) for r in rows]
            await _enrich(uow, summaries)
            await uow.commit()
        return JobListResult(jobs=summaries, total=len(rows))

    async def ranked(self, params: RankedJobParams) -> RankedJobListResult:
        """The shortlist: hard filters first, then policy-aware ranking.

        Ranking happens over a bounded recent window. The window is
        reported so the UI can say what was ranked; a job outside it is
        still reachable through search.
        """
        profile_id = params.search_profile_id or "default"
        async with self._container.uow_factory() as uow:
            profile = await uow.search_profiles.get(profile_id)
            default_policy = str(
                getattr(
                    self._container.config.ranking,
                    "credibility_policy",
                    DEFAULT_POLICY,
                )
            )
            policy: str = str(
                params.ranking_policy
                or (profile.ranking_policy if profile else None)
                or default_policy
            )
            filters = dict(
                params.filters
                or (profile.hard_filters if profile else {})
                or {}
            )
            hidden = (
                []
                if params.include_suppressed
                else await _hidden_job_ids(uow, self._container.clock.now())
            )
            # Headline counts describe the whole corpus; the ranking window and
            # the page size below only affect which rows are returned.
            corpus_total = await uow.jobs.count(exclude_job_ids=hidden)
            needs_attention = await uow.intelligence.assessments.count_latest_in_classes(
                (
                    RiskClass.NEEDS_VERIFICATION.value,
                    RiskClass.LIKELY_INACTIVE.value,
                ),
                exclude_job_ids=hidden,
            )
            since = _parse_iso(params.seen_since)
            new_since = (
                corpus_total
                if since is None
                else await uow.jobs.count(
                    exclude_job_ids=hidden, first_seen_after=since
                )
            )
            rows, _ = await uow.jobs.filtered_page(
                filters,
                query=params.query,
                limit=params.window,
                offset=0,
                exclude_job_ids=hidden,
            )
            summaries = [_summary(row) for row in rows]
            await _enrich(uow, summaries)
            await _attach_fit(uow, summaries, profile_id)
            await uow.commit()

        if params.ensure_fit and params.sort == "rank" and summaries:
            from app.application.commands.matching import MatchingUseCase

            missing = [item.job_id for item in summaries if item.fit_score is None]
            if missing:
                await MatchingUseCase(self._container).run(
                    profile_id, job_ids=missing[:200]
                )
                async with self._container.uow_factory() as uow:
                    await _attach_fit(uow, summaries, profile_id)
                    await uow.commit()

        hidden_count = 0
        visible: list[JobSummary] = []
        for item in summaries:
            state = visibility(
                item.risk_class, item.risk_confidence, policy=policy
            )
            item.hidden_by_policy = state == "hidden"
            item.rank_score = rank_score(
                item.fit_score, item.risk, item.risk_confidence, policy=policy
            )
            item.rank_reasons = list(
                explain(
                    fit=item.fit_score,
                    risk=item.risk,
                    confidence=item.risk_confidence,
                    risk_label=item.risk_label,
                    policy=policy,
                    visibility_state=state,
                ).reasons
            )
            if state == "hidden":
                hidden_count += 1
                if not params.include_hidden:
                    continue
            visible.append(item)

        if params.sort == "fit":
            visible.sort(key=lambda item: (-(item.fit_score or -1), item.title))
        elif params.sort == "credibility":
            visible.sort(key=lambda item: (-(item.credibility or -1), item.title))
        elif params.sort == "freshness":
            visible.sort(
                key=lambda item: item.posted_at or item.first_seen_at or "",
                reverse=True,
            )
        else:
            visible.sort(
                key=lambda item: (
                    item.rank_score is None,
                    -(item.rank_score or 0.0),
                    item.title,
                )
            )
        total = len(visible)
        page = visible[params.offset : params.offset + params.limit]
        return RankedJobListResult(
            jobs=page,
            total=total,
            profile_id=profile_id,
            policy=policy,
            window=len(summaries),
            window_truncated=len(summaries) >= params.window,
            hidden_by_policy=hidden_count,
            needs_attention=needs_attention,
            new_since=new_since,
        )

    async def list_saved(self) -> JobListResult:
        async with self._container.uow_factory() as uow:
            states = list(await uow.job_user_state.list(state="saved"))
            job_ids = [str(row["job_id"]) for row in states]
            rows = await uow.jobs.list_by_ids(job_ids) if job_ids else []
            summaries = [_summary(row) for row in rows]
            await _enrich(uow, summaries)
            await uow.commit()
        return JobListResult(jobs=summaries, total=len(summaries))

    async def verify_now(self, job_id: str) -> dict[str, Any]:
        """One polite endpoint check on demand (spec §15.4)."""
        from app.application.endpoint_verifier import EndpointVerifier

        return await EndpointVerifier(self._container).verify(
            job_ids=[job_id], force=True
        )

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
        """Previously seen / removed / duplicate / suppressed jobs (§8.3)."""
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
            suppressions = await uow.suppressions.list(limit=500)
            states = await uow.job_user_state.list(limit=500)
            already = {str(row.get("id")) for row in rows}
            extra_ids = [
                job_id
                for job_id in (
                    [record.job_id for record in suppressions]
                    + [str(state.get("job_id")) for state in states]
                )
                if job_id and job_id not in already
            ]
            extra = await uow.jobs.list_by_ids(extra_ids)
            await uow.commit()

        by_id = {str(row.get("id")): row for row in rows}
        for row in extra:
            by_id.setdefault(str(row.get("id")), row)
        seen: list[JobSummary] = []
        listed: set[str] = set()
        for row in rows:
            job_id = str(row.get("id"))
            status = str(row.get("posting_status") or "active")
            if job_id not in duplicate_ids and status == "active":
                continue
            related = _related_ids(relations.get(job_id, []), job_id)
            reason = "duplicate" if job_id in duplicate_ids else f"posting_{status}"
            seen.append(_summary(row, reason=reason, related_job_ids=related))
            listed.add(job_id)

        # Suppressed and dismissed jobs are never in the ordinary lists, so the
        # Seen view is where their explanation lives.
        reasons: dict[str, str] = {
            "active": "suppressed",
            "expired": "suppression_expired",
            "restored": "suppression_restored",
        }
        suppressed_job_ids = {record.job_id for record in suppressions}
        for record in suppressions:
            if record.job_id in listed:
                continue
            suppressed_row = by_id.get(record.job_id)
            if suppressed_row is None:
                continue
            seen.append(
                _summary(
                    suppressed_row, reason=reasons.get(record.state, "suppressed")
                )
            )
            listed.add(record.job_id)
        for state in states:
            job_id = str(state.get("job_id"))
            if job_id in listed or job_id in suppressed_job_ids:
                continue
            state_row = by_id.get(job_id)
            if state_row is None:
                continue
            seen.append(
                _summary(state_row, reason=str(state.get("state") or "seen"))
            )
            listed.add(job_id)
        return JobListResult(jobs=seen, total=len(seen))


async def _hidden_job_ids(uow: Any, now: Any) -> list[str]:
    """Job ids the default views must not show: suppressed plus dismissed.

    ``now`` is passed so a cooldown that ended stops hiding the posting even if
    the expiry sweep has not run yet (spec §8.2 rule 6).
    """
    suppressed = list(await uow.suppressions.active_job_ids(now))
    dismissed = list(await uow.job_user_state.hidden_job_ids())
    return list(dict.fromkeys([*suppressed, *dismissed]))


async def _attach_fit(
    uow: Any, summaries: Sequence[JobSummary], profile_id: str
) -> None:
    rows = await uow.matches.latest_for_jobs(
        [item.job_id for item in summaries], profile_id
    )
    for item in summaries:
        row = rows.get(item.job_id)
        if row is not None and row.get("fit_score") is not None:
            item.fit_score = round(float(row["fit_score"]), 4)


async def _enrich(uow: Any, summaries: Sequence[JobSummary]) -> None:
    """Attach the fields list views need without an N+1 per row.

    Missing evidence stays ``None`` rather than 0, so the UI can say
    "not assessed yet" instead of implying a verdict.
    """
    if not summaries:
        return
    job_ids = [item.job_id for item in summaries]
    assessments = await uow.intelligence.assessments.latest_for_jobs(job_ids)
    states = await uow.job_user_state.get_many(job_ids)
    companies = {
        str(row.get("id")): str(row.get("canonical_name") or "")
        for row in await uow.companies.list()
    }
    merged = canonical_map(await uow.company_merges.latest_actions())
    for item in summaries:
        canonical = merged.get(item.company_id or "", item.company_id or "")
        if canonical:
            item.company_id = canonical
        item.company_name = companies.get(canonical, companies.get(item.company_id or "", ""))
        item.saved = states.get(item.job_id) == "saved"
        assessment = assessments.get(item.job_id)
        if assessment is None:
            continue
        risk = float(assessment["risk"])
        confidence = float(assessment["confidence"])
        risk_class = str(assessment["class"])
        item.risk = round(risk, 4)
        item.risk_confidence = round(confidence, 4)
        item.risk_class = risk_class
        item.credibility = round(1.0 - risk, 4)
        try:
            item.risk_label = CLASS_LABELS[RiskClass(risk_class)]
        except ValueError:
            item.risk_label = None


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
        location_text=_location_text(row),
        workplace_type=str(row.get("workplace_type") or "unknown"),
        salary_min=_salary_value(row, "min_amount"),
        salary_max=_salary_value(row, "max_amount"),
        salary_currency=_salary_text(row, "currency"),
        posted_at=_text(row.get("posted_at")),
        first_seen_at=_text(row.get("first_seen_at")),
    )


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _location_text(row: Mapping[str, Any]) -> str:
    locations = row.get("locations") or []
    if not isinstance(locations, list) or not locations:
        return ""
    first = locations[0]
    if isinstance(first, Mapping):
        return str(first.get("raw") or first.get("city") or "")
    return str(first)


def _salary_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    salary = row.get("salary")
    return salary if isinstance(salary, Mapping) else {}


def _salary_value(row: Mapping[str, Any], key: str) -> int | None:
    value = _salary_payload(row).get(key)
    return int(value) if isinstance(value, (int, float)) else None


def _salary_text(row: Mapping[str, Any], key: str) -> str | None:
    value = _salary_payload(row).get(key)
    return str(value) if value else None


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


def _parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO timestamp from the client, ignoring anything malformed."""

    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _provenance_strategies(row: Mapping[str, Any]) -> list[str]:
    """The strategies that contributed to a job, in pipeline order."""
    seen: list[str] = []
    for entry in _provenance_entries(row):
        strategy = entry.get("strategy_id")
        if isinstance(strategy, str) and strategy not in seen:
            seen.append(strategy)
    return seen
