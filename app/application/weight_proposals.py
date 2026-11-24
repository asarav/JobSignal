"""Outcome-driven weight suggestions (M10).

Observed response history is compared with the stored fit dimensions. The result
is an association in your own applications, shown with its sample size, never a
cause and never an interview probability. Nothing changes until the user accepts
a suggestion, and each acceptance appends a row to ``fit_weight_versions`` so the
previous weighting stays recoverable.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from app.application.analytics import RESPONSE_KINDS
from app.domain.models.common import PortError
from app.domain.models.fit_weight import FitWeightVersionRecord
from app.domain.services.matching import (
    DEFAULT_WEIGHTS,
    DIMENSIONS,
    normalize_weights,
)

#: Below this many applications with a stored fit and a confirmed outcome, the
#: response is an observation with no suggestion attached.
MIN_PROPOSAL_SAMPLE = 20
#: Each side of the comparison needs at least this many applications.
MIN_PER_SIDE = 5
#: A dimension gap smaller than this is not worth suggesting a change for.
MIN_GAP = 0.05
#: How far a suggestion may move one dimension's weight.
STEP = 0.05


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


class WeightProposalService:
    def __init__(self, container: Any) -> None:
        self._container = container

    async def proposals(
        self, *, profile_id: str = "default", days: int = 365
    ) -> dict[str, Any]:
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            applications = list(await uow.applications.list(limit=1000))
            job_ids = [row.job_id for row in applications]
            fits = await uow.matches.latest_for_jobs(job_ids, profile_id)
            result_ids = [str(row.get("id")) for row in fits.values()]
            dimensions = await uow.matches.dimensions_for_results(result_ids)
            outcomes = {
                row.id: list(await uow.applications.list_outcomes(row.id))
                for row in applications
            }
            current = normalize_weights(
                dict(await uow.matches.get_weights(profile_id) or DEFAULT_WEIGHTS)
            )
            latest_version = await uow.weight_versions.latest_for_profile(profile_id)
            await uow.commit()

        by_result: dict[str, dict[str, float]] = {}
        for row in dimensions:
            if not row.get("available"):
                continue
            score = row.get("score")
            if not isinstance(score, (int, float)):
                continue
            by_result.setdefault(str(row.get("matching_result_id")), {})[
                str(row.get("dimension"))
            ] = float(score)

        responded: dict[str, list[float]] = {dimension: [] for dimension in DIMENSIONS}
        other: dict[str, list[float]] = {dimension: [] for dimension in DIMENSIONS}
        counted = responded_total = 0
        for application in applications:
            if application.applied_at is None:
                continue
            if (now - application.applied_at).days > days:
                continue
            fit = fits.get(application.job_id) or {}
            scores = by_result.get(str(fit.get("id")))
            if not scores:
                continue
            confirmed = [
                outcome
                for outcome in outcomes.get(application.id, [])
                if outcome.confirmed
            ]
            answered = any(outcome.kind in RESPONSE_KINDS for outcome in confirmed)
            counted += 1
            responded_total += int(answered)
            target = responded if answered else other
            for dimension, score in scores.items():
                if dimension in target:
                    target[dimension].append(score)

        other_total = counted - responded_total
        rows: list[dict[str, Any]] = []
        for dimension in DIMENSIONS:
            mean_responded = _mean(responded[dimension])
            mean_other = _mean(other[dimension])
            gap = (
                round(mean_responded - mean_other, 4)
                if mean_responded is not None and mean_other is not None
                else None
            )
            rows.append(
                {
                    "dimension": dimension,
                    "n_responded": len(responded[dimension]),
                    "n_not_responded": len(other[dimension]),
                    "mean_responded": round(mean_responded, 4)
                    if mean_responded is not None
                    else None,
                    "mean_not_responded": round(mean_other, 4)
                    if mean_other is not None
                    else None,
                    "gap": gap,
                    "insufficient": (
                        len(responded[dimension]) < MIN_PER_SIDE
                        or len(other[dimension]) < MIN_PER_SIDE
                    ),
                }
            )

        eligible = [
            row
            for row in rows
            if row["gap"] is not None and not row["insufficient"]
        ]
        best = max(eligible, key=lambda row: row["gap"], default=None)
        small_sample = (
            counted < MIN_PROPOSAL_SAMPLE
            or responded_total < MIN_PER_SIDE
            or other_total < MIN_PER_SIDE
            or best is None
            or float(best["gap"]) < MIN_GAP
        )
        proposal = None
        if not small_sample and best is not None:
            weights = _tilt(current, str(best["dimension"]))
            proposal = {
                "dimension": best["dimension"],
                "gap": best["gap"],
                "sample_size": counted,
                "n_responded": responded_total,
                "n_not_responded": other_total,
                "weights": weights,
                "changes": [
                    {
                        "dimension": dimension,
                        "from": round(current[dimension], 4),
                        "to": round(weights[dimension], 4),
                        "delta": round(weights[dimension] - current[dimension], 4),
                    }
                    for dimension in DIMENSIONS
                    if abs(weights[dimension] - current[dimension]) > 1e-9
                ],
                "requires_confirmation": True,
                "effect": (
                    "Accepting writes a new weight version and recomputes matches. "
                    "The previous weighting stays in the history."
                ),
            }

        return {
            "profile_id": profile_id,
            "generated_at": now.isoformat(),
            "window_days": days,
            "applications": counted,
            "responded": responded_total,
            "not_responded": other_total,
            "min_sample": MIN_PROPOSAL_SAMPLE,
            "min_per_side": MIN_PER_SIDE,
            "min_gap": MIN_GAP,
            "weights": current,
            "associations": rows,
            "insufficient_evidence": small_sample,
            "proposal": proposal,
            "latest_version": dict(latest_version) if latest_version else None,
            "note": (
                "These are associations in your own applications, with the sample "
                "size shown. They do not show that a dimension produced a reply, and "
                "nothing is applied unless you accept the suggestion."
            ),
        }

    async def apply(
        self,
        *,
        profile_id: str = "default",
        weights: Mapping[str, object],
        source: str = "proposal",
        evidence: Mapping[str, object] | None = None,
        reason: str = "",
    ) -> dict[str, Any]:
        if source not in {"manual", "proposal"}:
            raise PortError(
                "validation.invalid_params", "source must be 'manual' or 'proposal'"
            )
        if weights and not any(key in DIMENSIONS for key in weights):
            raise PortError(
                "validation.invalid_params",
                "weights must name at least one fit dimension",
            )
        for key, value in weights.items():
            if key not in DIMENSIONS:
                continue
            if not (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value >= 0
            ):
                raise PortError(
                    "validation.invalid_params",
                    f"weight for {key} must be a non-negative number",
                )
        normalized = normalize_weights(weights)
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            stored = await uow.matches.get_weights(profile_id)
            previous = normalize_weights(dict(stored or DEFAULT_WEIGHTS))
            record = FitWeightVersionRecord(
                id=f"fwt_{self._container.ids.new_id()[:16]}",
                profile_id=profile_id,
                weights=normalized,
                previous_weights=previous if stored else None,
                source=source,
                created_by="user",
                created_at=now,
                evidence={**dict(evidence or {}), **({"reason": reason} if reason else {})},
            )
            await uow.weight_versions.add(record)
            await uow.matches.set_weights(profile_id, normalized)
            await uow.commit()
        return {
            "ok": True,
            "profile_id": profile_id,
            "version_id": record.id,
            "weights": normalized,
            "previous_weights": record.previous_weights,
            "source": record.source,
            "created_at": now.isoformat(),
        }

    async def history(
        self, *, profile_id: str = "default", limit: int = 50
    ) -> dict[str, Any]:
        async with self._container.uow_factory() as uow:
            versions = [
                dict(row)
                for row in await uow.weight_versions.list_for_profile(
                    profile_id, limit=limit
                )
            ]
            current = normalize_weights(
                dict(await uow.matches.get_weights(profile_id) or DEFAULT_WEIGHTS)
            )
            await uow.commit()
        return {
            "profile_id": profile_id,
            "weights": current,
            "versions": versions,
            "note": (
                "Every accepted weighting is kept. The newest row is what ranking "
                "uses now; older rows show what it replaced."
            ),
        }


def _tilt(weights: dict[str, float], dimension: str) -> dict[str, float]:
    """Move ``STEP`` of weight toward one dimension and spread the rest."""
    adjusted = dict(weights)
    adjusted[dimension] = min(0.6, adjusted.get(dimension, 0.0) + STEP)
    others = [key for key in DIMENSIONS if key != dimension]
    total_others = sum(adjusted[key] for key in others)
    if total_others > 0:
        for key in others:
            adjusted[key] = adjusted[key] - STEP * (adjusted[key] / total_others)
    return normalize_weights(adjusted)


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


__all__ = [
    "MIN_GAP",
    "MIN_PER_SIDE",
    "MIN_PROPOSAL_SAMPLE",
    "WeightProposalService",
]
