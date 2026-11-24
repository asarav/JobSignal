"""Outcome analytics and risk calibration (M9, spec §19.8).

Both services describe what was observed. They never feed a number back into
ranking automatically: a calibration proposal is returned for the user to accept,
and ``no_response`` is an outcome record, not a risk signal.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.application.intelligence import wilson_interval

#: Outcomes that count as the employer responding. ``no_response`` is recorded by
#: the user but is not evidence of anything about the posting.
RESPONSE_KINDS = frozenset(
    {"acknowledged", "recruiter_contact", "interview", "rejection", "offer", "accepted"}
)
INACTIVE_ENDPOINT_STATES = frozenset({"gone_404_410", "closed_message"})
MIN_SAMPLE = 5
CALIBRATION_MIN_SAMPLE = 30


def _rate(successes: int, total: int) -> dict[str, Any]:
    if total == 0:
        return {"n": 0, "successes": 0, "rate": None, "interval": None, "insufficient": True}
    low, high = wilson_interval(successes, total)
    return {
        "n": total,
        "successes": successes,
        "rate": round(successes / total, 4),
        "interval": [round(low, 4), round(high, 4)],
        "insufficient": total < MIN_SAMPLE,
    }


def _group(name: str, buckets: Mapping[str, list[bool]]) -> list[dict[str, Any]]:
    return [
        {"group": name, "key": key, **_rate(sum(values), len(values))}
        for key, values in sorted(buckets.items())
    ]


class OutcomeAnalyticsService:
    def __init__(self, container: Any) -> None:
        self._container = container

    async def get_analytics(
        self, *, days: int = 365, profile_id: str = "default"
    ) -> dict[str, Any]:
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            applications = list(await uow.applications.list(limit=500))
            job_ids = [row.job_id for row in applications]
            risks = await uow.intelligence.assessments.latest_for_jobs(job_ids)
            fits = await uow.matches.latest_for_jobs(job_ids, profile_id)
            jobs = {
                row["id"]: dict(row) for row in await uow.jobs.list_by_ids(job_ids)
            }
            endpoints = {}
            summaries = {}
            reposts = {}
            for job_id in job_ids:
                endpoints[job_id] = await uow.intelligence.endpoints.latest(job_id)
                company_id = jobs.get(job_id, {}).get("company_id")
                if company_id and company_id not in summaries:
                    summaries[company_id] = await uow.intelligence.company_summaries.latest(
                        str(company_id)
                    )
                reposts[job_id] = len(await uow.dedup.list_repost_events(job_id))
            outcomes = {
                row.id: list(await uow.applications.list_outcomes(row.id))
                for row in applications
            }
            await uow.commit()

        by_risk: dict[str, list[bool]] = {}
        by_endpoint: dict[str, list[bool]] = {}
        by_reposts: dict[str, list[bool]] = {}
        by_tier: dict[str, list[bool]] = {}
        by_fit: dict[str, list[bool]] = {}
        by_age: dict[str, list[bool]] = {}
        responded_total = 0
        counted = 0

        for application in applications:
            if application.applied_at is None:
                continue
            if (now - application.applied_at).days > days:
                continue
            confirmed = [
                outcome
                for outcome in outcomes.get(application.id, [])
                if outcome.confirmed
            ]
            responded = any(outcome.kind in RESPONSE_KINDS for outcome in confirmed)
            counted += 1
            responded_total += int(responded)

            risk = risks.get(application.job_id) or {}
            risk_class = str(risk.get("class") or "unclassified")
            label = risk_class.replace("_", " ")
            by_risk.setdefault(label, []).append(responded)

            endpoint = endpoints.get(application.job_id) or {}
            state = str(endpoint.get("state") or "unchecked")
            by_endpoint.setdefault(state.replace("_", " "), []).append(responded)

            count = reposts.get(application.job_id, 0)
            bucket = "0" if count == 0 else "1" if count == 1 else "2+"
            by_reposts.setdefault(bucket, []).append(responded)

            job = jobs.get(application.job_id, {})
            summary = summaries.get(str(job.get("company_id") or "")) or {}
            tier = str(summary.get("tier") or "insufficient")
            by_tier.setdefault(tier, []).append(responded)

            fit_row = fits.get(application.job_id) or {}
            fit = fit_row.get("fit_score")
            if isinstance(fit, (int, float)):
                lower = min(0.8, int(float(fit) * 5) / 5)
                by_fit.setdefault(f"{lower:.1f}–{lower + 0.2:.1f}", []).append(responded)

            posted = job.get("posted_at") or job.get("first_seen_at")
            parsed = _parse(posted)
            if parsed is not None:
                age = max(0, (application.applied_at - parsed).days)
                bucket = "0–7 days" if age <= 7 else "8–30 days" if age <= 30 else "31+ days"
                by_age.setdefault(bucket, []).append(responded)

        return {
            "generated_at": now.isoformat(),
            "window_days": days,
            "applications": counted,
            "responded": responded_total,
            "overall": _rate(responded_total, counted),
            "min_sample": MIN_SAMPLE,
            "groups": [
                *_group("risk_class", by_risk),
                *_group("endpoint_state", by_endpoint),
                *_group("repost_count", by_reposts),
                *_group("company_tier", by_tier),
                *_group("fit_band", by_fit),
                *_group("posting_age_at_application", by_age),
            ],
            "note": (
                "Response rates describe your own history. Groups with fewer than "
                f"{MIN_SAMPLE} applications hide their rate, and no group changes "
                "ranking automatically."
            ),
        }


class CalibrationService:
    """Compare predicted posting risk with what endpoint checks observed."""

    def __init__(self, container: Any) -> None:
        self._container = container

    async def get_calibration(self) -> dict[str, Any]:
        async with self._container.uow_factory() as uow:
            rows, _ = await uow.jobs.filtered_page({}, limit=500)
            job_ids = [str(row["id"]) for row in rows]
            assessments = await uow.intelligence.assessments.latest_for_jobs(job_ids)
            observations = {
                job_id: await uow.intelligence.endpoints.latest(job_id)
                for job_id in job_ids
            }
            await uow.commit()

        buckets: dict[str, dict[str, list[float]]] = {}
        sample = 0
        closed_total = 0
        for job_id, assessment in assessments.items():
            observation = observations.get(job_id)
            if not observation:
                continue
            state = str(observation.get("state") or "")
            if state not in (INACTIVE_ENDPOINT_STATES | {"ok", "form_unavailable"}):
                continue
            risk = float(assessment.get("risk") or 0.0)
            key = f"{min(0.8, int(risk * 5) / 5):.1f}–{min(1.0, int(risk * 5) / 5 + 0.2):.1f}"
            closed = state in INACTIVE_ENDPOINT_STATES
            buckets.setdefault(key, {"risks": [], "closed": []})
            buckets[key]["risks"].append(risk)
            buckets[key]["closed"].append(1.0 if closed else 0.0)
            sample += 1
            closed_total += int(closed)

        table = []
        for key, values in sorted(buckets.items()):
            n = len(values["closed"])
            entry: dict[str, Any] = {
                "risk_band": key,
                "predicted": round(
                    sum(values["risks"]) / n if n else 0.0, 4
                ),
                **_rate(int(sum(values["closed"])), n),
            }
            table.append(entry)

        proposal = None
        if sample >= CALIBRATION_MIN_SAMPLE:
            proposal = {
                "suggested_prior_risk": round(closed_total / sample, 4),
                "sample_size": sample,
                "requires_confirmation": True,
                "effect": (
                    "Saving this writes a new model version; existing assessments and "
                    "their history are kept unchanged."
                ),
            }
        return {
            "sample_size": sample,
            "min_sample": CALIBRATION_MIN_SAMPLE,
            "observed_closed": closed_total,
            "buckets": table,
            "proposal": proposal,
            "note": (
                "Calibration compares predicted risk with endpoint observations only. "
                "A missing or blocked check is excluded, and nothing changes unless "
                "you accept a new model version."
            ),
        }


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = [
    "CALIBRATION_MIN_SAMPLE",
    "CalibrationService",
    "INACTIVE_ENDPOINT_STATES",
    "MIN_SAMPLE",
    "OutcomeAnalyticsService",
    "RESPONSE_KINDS",
    "_rate",
]
