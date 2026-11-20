"""Matching use case (M5)."""
from __future__ import annotations

import hashlib
import uuid
from typing import Any

from app.application.container import Container
from app.domain.models.matching import ScoringConfig
from app.domain.services.matching import (
    CompensationStrategy,
    ExperienceSeniorityStrategy,
    FreshnessStrategy,
    LocationWorkplaceStrategy,
    ResponsibilitiesStrategy,
    SkillsResumeStrategy,
    WeightedScoringModel,
)

_STRATEGIES = [
    SkillsResumeStrategy(),
    ExperienceSeniorityStrategy(),
    ResponsibilitiesStrategy(),
    LocationWorkplaceStrategy(),
    CompensationStrategy(),
    FreshnessStrategy(),
]


class MatchingUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container

    async def run(self, profile_id: str = "default", job_ids: list[str] | None = None) -> list[dict[str, Any]]:
        async with self._container.uow_factory() as uow:
            profile = await uow.resumes.get_profile(profile_id)
            version_id = profile.get("active_resume_version_id") if profile else None
            skills: list[str] = []
            years = None
            if version_id:
                sks = await uow.resumes.get_skills(str(version_id))
                skills = [str(s["keyword_id"]) for s in sks]
                overrides = await uow.resumes.get_overrides(profile_id)
                years = overrides.get("years_experience_total")
            # candidate profile as simple namespace
            from types import SimpleNamespace
            cand = SimpleNamespace(skills=skills, years_experience_total=years)

            if job_ids is None:
                jobs = await uow.jobs.list(limit=200)
            else:
                jobs = []
                for jid in job_ids:
                    j = await uow.jobs.get(jid)
                    if j:
                        jobs.append(j)

            model = WeightedScoringModel()
            cfg = ScoringConfig(model_id="fit.weighted_v1", model_version="1.0.0", weights={})
            results = []
            for job in jobs:
                job_dict = dict(job)
                r = model.evaluate(cand, job_dict, None, _STRATEGIES, cfg)
                result_id = f"mr_{uuid.uuid4().hex[:12]}"
                dims: list[dict[str, Any]] = [
                    {
                        "dimension": d.dimension,
                        "strategy_id": d.strategy_id,
                        "strategy_version": d.strategy_version,
                        "score": d.score,
                        "available": d.available,
                        "weight": d.weight,
                        "details": {},
                    }
                    for d in r.dimensions
                ]
                await uow.matches.save_result(
                    result_id, str(job.get("id")), profile_id, str(version_id) if version_id else None,
                    r.model_id, r.model_version, dict(r.weights), hashlib.sha256(str(job.get("id")).encode()).hexdigest(),
                    r.fit_score, r.dimension_coverage, dims, r.evaluated_at.isoformat() if r.evaluated_at else "",
                )
                results.append({"result_id": result_id, "job_id": job.get("id"), "fit_score": r.fit_score, "dimension_coverage": r.dimension_coverage})
            await uow.commit()
        return results

    async def get_results(self, profile_id: str = "default", limit: int = 50) -> list[dict[str, Any]]:
        async with self._container.uow_factory() as uow:
            rows = await uow.matches.list_results(profile_id, limit=limit)
            await uow.commit()
        return [dict(r) for r in rows]

    async def update_weights(self, profile_id: str, weights: dict[str, Any]) -> None:
        # no-op persistence in this slice; weights are per-result
        return None
