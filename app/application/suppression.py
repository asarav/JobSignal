"""Glue between the suppression engine and the unit of work (spec §8.2).

The domain decides; this module loads the policy, builds the candidate from a
stored job row and persists the decision. Ingestion (``sources.py``), policy
changes and application changes all funnel through here so a job is evaluated
the same way no matter what triggered it.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.domain.models.application import (
    AppliedJob,
    SuppressionCandidate,
    SuppressionDecision,
    SuppressionPolicy,
    SuppressionRecord,
)
from app.domain.ports.repositories import UnitOfWork
from app.domain.services.suppression import evaluate_suppression

POLICY_SETTING_KEY = "suppression_policy"


def policy_from_config(config: Any) -> SuppressionPolicy:
    """The shipped defaults plus whatever ``AppConfig.suppression`` says."""
    settings = getattr(config, "suppression", None)
    values: dict[str, object] = {}
    for field in (
        "exact_repost_suppress",
        "similar_role_cooldown_days",
        "similar_role_threshold",
        "company_wide_cooldown_enabled",
        "company_wide_cooldown_days",
    ):
        value = getattr(settings, field, None) if settings is not None else None
        if value is not None:
            values[field] = value
    # Config fixes the defaults; the stored policy (user edits) wins over it.
    return SuppressionPolicy.from_mapping({**SuppressionPolicy().to_mapping(), **values})


async def load_policy(uow: UnitOfWork, config: Any) -> SuppressionPolicy:
    """Stored policy when the user changed it, else the configured defaults."""
    stored = await uow.suppressions.get_policy()
    if stored:
        return SuppressionPolicy.from_mapping(stored)
    return policy_from_config(config)


def candidate_from_row(row: Mapping[str, Any]) -> SuppressionCandidate:
    locations = row.get("locations")
    if not isinstance(locations, (list, tuple)):
        locations = row.get("job_locations") or ()
    family = row.get("title_family")
    title_family = family if isinstance(family, str) else None
    department_value = row.get("department")
    department = department_value if isinstance(department_value, str) else None
    return SuppressionCandidate(
        job_id=str(row.get("id") or row.get("job_id") or ""),
        company_id=str(row.get("company_id") or ""),
        title=str(row.get("title") or row.get("job_title") or ""),
        title_family=title_family,
        description_text=str(
            row.get("description_text") or row.get("job_description_text") or ""
        ),
        canonical_url=str(row.get("canonical_url") or row.get("job_canonical_url") or ""),
        department=department,
        locations=tuple(
            entry for entry in locations if isinstance(entry, Mapping)
        ),
        workplace_type=str(row.get("workplace_type") or row.get("job_workplace_type") or "unknown"),
        related_job_ids=tuple(
            str(value) for value in (row.get("related_job_ids") or ()) if value
        ),
    )


def _record(
    candidate: SuppressionCandidate,
    decision: SuppressionDecision,
    *,
    now: datetime,
    record_id: str | None = None,
) -> SuppressionRecord:
    return SuppressionRecord(
        id=record_id or str(uuid.uuid4()),
        job_id=candidate.job_id,
        application_id=decision.application_id,
        state="active",
        reason=decision.reason,
        explanation=decision.explanation,
        similarity=decision.similarity,
        evidence=dict(decision.evidence),
        policy=dict(decision.policy),
        expires_at=decision.expires_at,
        created_at=now,
        updated_at=now,
    )


async def evaluate_and_store(
    uow: UnitOfWork,
    candidate: SuppressionCandidate,
    *,
    applied: list[AppliedJob] | None = None,
    policy: SuppressionPolicy,
    now: datetime,
) -> SuppressionRecord | None:
    """Evaluate one job; store a decision when it is suppressed."""
    if not candidate.job_id or not candidate.company_id:
        return None
    history = applied if applied is not None else list(await uow.applications.list_applied())
    decision = evaluate_suppression(candidate, history, policy, now=now)
    if decision is None:
        return None
    record = _record(candidate, decision, now=now)
    await uow.suppressions.save(record)
    return record


async def evaluate_job(
    uow: UnitOfWork, job_id: str, *, policy: SuppressionPolicy, now: datetime
) -> SuppressionRecord | None:
    row = await uow.jobs.get(job_id)
    if row is None:
        return None
    return await evaluate_and_store(
        uow, candidate_from_row(row), policy=policy, now=now
    )


async def evaluate_company(
    uow: UnitOfWork, company_id: str, *, policy: SuppressionPolicy, now: datetime
) -> int:
    """Re-evaluate the company's stored jobs after an application reached applied."""
    applied = list(await uow.applications.list_applied(company_id=company_id))
    if not applied:
        return 0
    rows = await uow.jobs.list(company_id=company_id, limit=1000)
    stored = 0
    for row in rows:
        if await evaluate_and_store(
            uow, candidate_from_row(row), applied=applied, policy=policy, now=now
        ):
            stored += 1
    return stored


async def rebuild(uow: UnitOfWork, *, policy: SuppressionPolicy, now: datetime) -> int:
    """Re-derive every suppression after a policy change.

    Jobs that no longer match lose their non-restored rows so they reappear;
    user restores survive (spec §8.2 rule 8).
    """
    applied = list(await uow.applications.list_applied())
    by_company: dict[str, list[AppliedJob]] = {}
    for entry in applied:
        by_company.setdefault(entry.company_id, []).append(entry)
    changed = 0
    for company_id, history in by_company.items():
        rows = await uow.jobs.list(company_id=company_id, limit=1000)
        for row in rows:
            candidate = candidate_from_row(row)
            if not candidate.job_id:
                continue
            decision = evaluate_suppression(candidate, history, policy, now=now)
            if decision is None:
                changed += await uow.suppressions.clear_job(candidate.job_id)
                continue
            await uow.suppressions.save(
                _record(candidate, decision, now=now)
            )
            changed += 1
    return changed


def utc_now() -> datetime:
    return datetime.now(UTC)
