"""Per-posting keyword persistence and insight maths (M9).

Keywords are extracted once per posting revision and stored append-only, so the
normal evaluation pipeline fills prevalence without anyone pressing a button. The
insight layer only describes what it saw: a sample below the threshold shows its
`n` and hides its rate.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from app.domain.models.common import PortError
from app.domain.models.job_keyword import (
    JobKeywordRecord,
    JobKeywordRevisionRecord,
)
from app.domain.services.company_resolution import canonical_map
from app.domain.services.keywords import KeywordExtractor

#: Below this many postings a prevalence or coverage row shows its count but no
#: rate, so a handful of postings is never presented as a trend.
MIN_SAMPLE = 10


def revision_hash_for(job: Mapping[str, Any]) -> str:
    content = str(job.get("content_hash") or "")
    if content:
        return content
    payload = "\n".join(
        [
            str(job.get("title") or ""),
            str(job.get("description_text") or ""),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def extraction_text(job: Mapping[str, Any]) -> str:
    parts = [
        str(job.get("title") or ""),
        str(job.get("description_text") or ""),
    ]
    for key in ("required_skills", "preferred_skills"):
        values = job.get(key)
        if isinstance(values, (list, tuple)):
            parts.append(" ".join(str(value) for value in values))
    return "\n".join(part for part in parts if part)


class KeywordPersistenceService:
    def __init__(self, container: Any) -> None:
        self._container = container
        self._extractor = KeywordExtractor()

    async def persist_for_job(
        self, uow: Any, job: Mapping[str, Any], now: datetime
    ) -> bool:
        """Append a revision when the posting content changed. Idempotent."""
        job_id = str(job.get("id") or "")
        if not job_id:
            return False
        revision_hash = revision_hash_for(job)
        latest = await uow.keywords.latest_revision(job_id)
        if latest is not None and str(latest.get("revision_hash")) == revision_hash:
            return False
        text = extraction_text(job)
        found = self._extractor.extract(text)
        if not found:
            found = []
        taxonomy_version = self._extractor.normalizer.taxonomy_version()
        revision = JobKeywordRevisionRecord(
            id=f"jkr_{self._container.ids.new_id()[:16]}",
            job_id=job_id,
            revision_hash=revision_hash,
            taxonomy_version=taxonomy_version,
            extracted_at=now,
            keyword_count=len(found),
        )
        mentions = _mention_counts(self._extractor, text)
        records = [
            JobKeywordRecord(
                id=f"jk_{self._container.ids.new_id()[:16]}",
                job_id=job_id,
                revision_hash=revision_hash,
                keyword_id=keyword_id,
                taxonomy_version=taxonomy_version,
                extracted_at=now,
                mentions=mentions.get(keyword_id, 1),
            )
            for keyword_id in found
        ]
        await uow.keywords.add_revision(revision, records)
        return True

    async def backfill(self, *, limit: int = 100) -> dict[str, Any]:
        """Persist keywords for postings that have no revision yet.

        Resumable: each call takes the next batch that is still missing a
        revision, so an interrupted pass simply continues next time.
        """
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            job_ids = list(await uow.keywords.jobs_missing_revisions(limit=limit))
            processed = 0
            for job_id in job_ids:
                job = await uow.jobs.get(job_id)
                if job is None:
                    continue
                if await self.persist_for_job(uow, job, now):
                    processed += 1
            remaining = len(await uow.keywords.jobs_missing_revisions(limit=limit))
            await uow.commit()
        return {"processed": processed, "remaining": remaining}


def _mention_counts(extractor: KeywordExtractor, text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for keyword_id, _start, _end in extractor.mentions_with_spans(text):
        counts[keyword_id] = counts.get(keyword_id, 0) + 1
    return counts


class KeywordInsightsService:
    def __init__(self, container: Any) -> None:
        self._container = container

    async def _scope_job_ids(
        self, uow: Any, company_id: str | None
    ) -> list[str]:
        if company_id is None:
            return list(await uow.keywords.jobs_with_revisions(limit=5000))
        rows = await uow.company_merges.latest_actions()
        merged = canonical_map(rows)
        children = [key for key, value in merged.items() if value == company_id]
        return list(
            await uow.keywords.jobs_with_revisions(
                company_ids=[company_id, *children], limit=5000
            )
        )

    async def prevalence(
        self,
        *,
        company_id: str | None = None,
        min_sample: int = MIN_SAMPLE,
        limit: int = 50,
    ) -> dict[str, Any]:
        async with self._container.uow_factory() as uow:
            resolved = (
                await self._company_survivor(uow, company_id)
                if company_id
                else None
            )
            job_ids = await self._scope_job_ids(uow, resolved)
            mentions = await uow.keywords.keywords_with_mentions(job_ids)
            await uow.commit()
        from app.application.intelligence import wilson_interval

        n_jobs = len(job_ids)
        counts: dict[str, int] = {}
        for values in mentions.values():
            for keyword_id in values:
                counts[keyword_id] = counts.get(keyword_id, 0) + 1
        rows = []
        for keyword_id, jobs in counts.items():
            if n_jobs > 0:
                low, high = wilson_interval(jobs, n_jobs)
            else:
                low = high = 0.0
            rows.append(
                {
                    "keyword_id": keyword_id,
                    "jobs": jobs,
                    "n": n_jobs,
                    "prevalence": round(jobs / n_jobs, 4) if n_jobs else None,
                    "interval": [round(low, 4), round(high, 4)],
                    "insufficient": n_jobs < min_sample,
                }
            )
        rows.sort(key=lambda row: (-int(str(row["jobs"])), str(row["keyword_id"])))
        return {
            "scope": "company" if resolved else "global",
            "company_id": resolved,
            "n_jobs": n_jobs,
            "min_sample": min_sample,
            "small_sample": n_jobs < min_sample,
            "keywords": rows[:limit],
            "note": (
                "Prevalence is the share of postings whose latest revision mentions "
                "a keyword. Below the sample threshold the count is shown but the "
                "rate is not a trend."
            ),
        }

    async def trends(
        self,
        *,
        company_id: str | None = None,
        bucket: str = "month",
        periods: int = 12,
        min_sample: int = MIN_SAMPLE,
        limit: int = 8,
        keyword_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """Per-period mention counts and coverage from stored keyword history.

        Two batched reads feed the whole response: the latest revision per job and
        the keyword rows for the jobs in scope. No query is issued per keyword or
        per job.
        """
        if bucket not in {"month", "week"}:
            raise PortError(
                "validation.invalid_params",
                "bucket must be 'month' or 'week'",
            )
        async with self._container.uow_factory() as uow:
            resolved = (
                await self._company_survivor(uow, company_id)
                if company_id
                else None
            )
            job_ids = await self._scope_job_ids(uow, resolved)
            revisions = await uow.keywords.latest_revisions_for_jobs(job_ids)
            rows = await uow.keywords.keyword_rows_for_jobs(job_ids)
            await uow.commit()

        latest_hash = {
            str(job_id): str(revision.get("revision_hash"))
            for job_id, revision in revisions.items()
        }
        jobs_by_period: dict[str, set[str]] = {}
        keyword_jobs: dict[tuple[str, str], set[str]] = {}
        keyword_mentions: dict[tuple[str, str], int] = {}
        totals: dict[str, int] = {}
        for row in rows:
            job_id = str(row.get("job_id") or "")
            if latest_hash.get(job_id) != str(row.get("revision_hash")):
                continue
            revision = revisions.get(job_id) or {}
            observed = _parse_iso(revision.get("extracted_at"))
            if observed is None:
                continue
            period = _period_key(observed, bucket)
            keyword_id = str(row.get("keyword_id") or "")
            if not keyword_id:
                continue
            jobs_by_period.setdefault(period, set()).add(job_id)
            keyword_jobs.setdefault((period, keyword_id), set()).add(job_id)
            keyword_mentions[(period, keyword_id)] = keyword_mentions.get(
                (period, keyword_id), 0
            ) + int(row.get("mentions") or 1)
            totals[keyword_id] = totals.get(keyword_id, 0) + 1

        if keyword_ids:
            selected = [str(value) for value in keyword_ids if str(value) in totals]
        else:
            ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
            selected = [keyword_id for keyword_id, _ in ranked[:limit]]

        now = self._container.clock.now()
        timeline = _period_sequence(now, bucket, periods)
        timeline = sorted(set(timeline) | set(jobs_by_period))
        result_rows: list[dict[str, Any]] = []
        for period in timeline:
            period_jobs = jobs_by_period.get(period, set())
            n_jobs = len(period_jobs)
            insufficient = n_jobs < min_sample
            values: list[dict[str, Any]] = []
            for keyword_id in selected:
                jobs = len(keyword_jobs.get((period, keyword_id), set()))
                mentions = keyword_mentions.get((period, keyword_id), 0)
                if n_jobs:
                    low, high = _wilson(jobs, n_jobs)
                    prevalence = round(jobs / n_jobs, 4)
                else:
                    low = high = 0.0
                    prevalence = None
                values.append(
                    {
                        "keyword_id": keyword_id,
                        "jobs": jobs,
                        "mentions": mentions,
                        "prevalence": None if insufficient else prevalence,
                        "interval": (
                            None
                            if insufficient
                            else [round(low, 4), round(high, 4)]
                        ),
                        "insufficient": insufficient,
                    }
                )
            result_rows.append(
                {
                    "period": period,
                    "n_jobs": n_jobs,
                    "insufficient": insufficient,
                    "no_data": n_jobs == 0,
                    "keywords": values,
                }
            )

        return {
            "scope": "company" if resolved else "global",
            "company_id": resolved,
            "bucket": bucket,
            "min_sample": min_sample,
            "keywords": [
                {
                    "keyword_id": keyword_id,
                    "jobs": totals.get(keyword_id, 0),
                }
                for keyword_id in selected
            ],
            "periods": result_rows,
            "n_jobs": len(job_ids),
            "note": (
                "Trends count the postings whose latest stored revision mentions a "
                "keyword in each period. A period below the sample threshold keeps "
                "its counts but no rate, and a period with no postings is shown as "
                "missing evidence, not zero demand."
            ),
        }

    async def coverage(
        self,
        *,
        company_id: str | None = None,
        profile_id: str = "default",
        min_sample: int = MIN_SAMPLE,
        limit: int = 50,
    ) -> dict[str, Any]:
        payload = await self.prevalence(
            company_id=company_id, min_sample=min_sample, limit=limit
        )
        async with self._container.uow_factory() as uow:
            profile = await uow.resumes.get_profile(profile_id)
            version_id = (
                str(profile.get("active_resume_version_id") or "") if profile else ""
            )
            skills: set[str] = set()
            if version_id:
                rows = await uow.resumes.get_skills(version_id)
                skills = {str(row["keyword_id"]) for row in rows}
            overrides = await uow.resumes.get_overrides(profile_id)
            for value in overrides.get("skills_added", []) or []:
                skills.add(str(value))
            for keyword_id, status in (
                await uow.resumes.get_skill_status(profile_id)
            ).items():
                if status in {"dismissed", "not_interested"}:
                    skills.discard(keyword_id)
                elif status in {"have", "learning"}:
                    skills.add(keyword_id)
            await uow.commit()
        top = [row for row in payload["keywords"] if isinstance(row, Mapping)]
        covered = [row for row in top if str(row["keyword_id"]) in skills]
        missing = [row for row in top if str(row["keyword_id"]) not in skills]
        ratio = len(covered) / len(top) if top else None
        return {
            **payload,
            "profile_id": profile_id,
            "candidate_skills": len(skills),
            "covered": covered,
            "missing": missing,
            "coverage_ratio": round(ratio, 4) if ratio is not None else None,
            "note": (
                "Coverage compares the most-mentioned keywords in scope with the "
                "skills on your profile. It is descriptive: a missing skill is not "
                "a requirement."
            ),
        }

    async def _company_survivor(self, uow: Any, company_id: str) -> str:
        rows = await uow.company_merges.latest_actions()
        merged = canonical_map(rows)
        return merged.get(company_id, company_id)


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _period_key(moment: datetime, bucket: str) -> str:
    if bucket == "week":
        iso = moment.isocalendar()
        return f"{iso.year:04d}-W{iso.week:02d}"
    return f"{moment.year:04d}-{moment.month:02d}"


def _period_sequence(now: datetime, bucket: str, count: int) -> list[str]:
    """The last ``count`` period keys, oldest first, ending at the current one."""
    count = max(1, int(count))
    keys: list[str] = []
    if bucket == "week":
        day = now.date() - timedelta(days=now.date().weekday())
        for offset in range(count - 1, -1, -1):
            iso = (day - timedelta(days=7 * offset)).isocalendar()
            keys.append(f"{iso.year:04d}-W{iso.week:02d}")
        return keys
    index = now.year * 12 + (now.month - 1)
    for offset in range(count - 1, -1, -1):
        value = index - offset
        keys.append(f"{value // 12:04d}-{value % 12 + 1:02d}")
    return keys


def _wilson(jobs: int, n_jobs: int) -> tuple[float, float]:
    from app.application.intelligence import wilson_interval

    return wilson_interval(jobs, n_jobs)


__all__ = [
    "MIN_SAMPLE",
    "KeywordInsightsService",
    "KeywordPersistenceService",
    "extraction_text",
    "revision_hash_for",
]
