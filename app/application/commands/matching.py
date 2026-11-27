"""Matching use case (M5)."""
from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any

from app.application.container import Container
from app.application.requirements import ensure_requirements, location_text
from app.domain.models.fit_weight import FitWeightVersionRecord
from app.domain.models.matching import ScoringConfig
from app.domain.services.keywords import normalize_skill_ids
from app.domain.services.location_prefs import job_in_area
from app.domain.services.matching import (
    DEFAULT_WEIGHTS,
    CompensationStrategy,
    ExperienceSeniorityStrategy,
    FreshnessStrategy,
    LocationWorkplaceStrategy,
    ResponsibilitiesStrategy,
    SkillsResumeStrategy,
    WeightedScoringModel,
    normalize_weights,
    required_skill_coverage,
)

_STRATEGIES = [
    SkillsResumeStrategy(),
    ExperienceSeniorityStrategy(),
    ResponsibilitiesStrategy(),
    LocationWorkplaceStrategy(),
    CompensationStrategy(),
    FreshnessStrategy(),
]
_MODEL = WeightedScoringModel()

#: Jobs one unbounded run scores, newest first.  The corpus can be much larger;
#: the summary reports the truncation instead of implying full coverage.
MATCHING_JOB_WINDOW = 200


class MatchingUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container

    async def run(
        self, profile_id: str = "default", job_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Score jobs and return the per-job results."""
        results, _summary = await self.run_with_summary(profile_id, job_ids)
        return results

    async def run_with_summary(
        self, profile_id: str = "default", job_ids: list[str] | None = None
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Score jobs and report what was considered.

        An empty or omitted ``job_ids`` means "everything collected": the UI
        sends an empty list for its Recompute button, and an empty list must
        never be read as an explicit empty target set.
        """
        explicit = [str(job_id) for job_id in (job_ids or []) if job_id]
        async with self._container.uow_factory() as uow:
            profile = await uow.resumes.get_profile(profile_id)
            version_id = str(profile.get("active_resume_version_id") or "") if profile else ""
            version = await uow.resumes.get_version(version_id) if version_id else None
            parsed = _parsed_json(version.get("parsed_json") if version else None)
            skills: list[str] = []
            skill_evidence: dict[str, str] = {}
            if version_id:
                rows = await uow.resumes.get_skills(version_id)
                skills = [str(row["keyword_id"]) for row in rows]
                skill_evidence = {str(row["keyword_id"]): str(row["evidence"]) for row in rows}
            overrides = await uow.resumes.get_overrides(profile_id)
            statuses = await uow.resumes.get_skill_status(profile_id)
            for key in normalize_skill_ids(overrides.get("skills_added")):
                if key not in skill_evidence:
                    skills.append(key)
                    skill_evidence[key] = "explicit"
            years = overrides.get("years_experience_total")
            preferences = overrides.get("preferences")
            if not isinstance(preferences, dict):
                preferences = overrides
            titles = _titles(overrides, parsed)
            cand = SimpleNamespace(
                skills=skills,
                skill_evidence=skill_evidence,
                skill_status=dict(statuses),
                years_experience_total=years,
                resume_text=str(parsed.get("text") or ""),
                titles=titles,
                preferences=preferences,
            )
            weights = normalize_weights(
                dict(await uow.matches.get_weights(profile_id) or DEFAULT_WEIGHTS)
            )
            profile_hash = _profile_hash(version, overrides, statuses, skills)
            cfg = ScoringConfig(
                model_id=_MODEL.id, model_version=_MODEL.version, weights=weights
            )

            jobs: Sequence[Mapping[str, object]]
            if explicit:
                jobs = []
                for job_id in explicit:
                    job = await uow.jobs.get(job_id)
                    if job:
                        jobs.append(job)
                available = len(jobs)
                truncated = False
            else:
                jobs = await uow.jobs.list(limit=MATCHING_JOB_WINDOW)
                available = await uow.jobs.count()
                truncated = available > len(jobs)

            results: list[dict[str, Any]] = []
            scored = 0
            reused = 0
            for job in jobs:
                job_dict = await ensure_requirements(uow, job)
                job_id = str(job_dict.get("id"))
                input_hash = _input_hash(job_dict, profile_hash, weights)
                existing = await uow.matches.find_result(profile_id, job_id, input_hash)
                if existing is not None:
                    results.append(_summary(existing, reused=True))
                    reused += 1
                    continue
                match = _MODEL.evaluate(cand, job_dict, None, _STRATEGIES, cfg)
                await uow.matches.delete_results(profile_id, job_id)
                result_id = f"mr_{uuid.uuid4().hex[:12]}"
                dimensions = [
                    {
                        "dimension": dimension.dimension,
                        "strategy_id": dimension.strategy_id,
                        "strategy_version": dimension.strategy_version,
                        "score": dimension.score,
                        "available": dimension.available,
                        "weight": weights.get(dimension.dimension, dimension.weight),
                        "details": {
                            "summary": dimension.explanation.summary
                            if dimension.explanation
                            else "",
                            "reasons": [
                                reason.message for reason in dimension.explanation.reasons
                            ]
                            if dimension.explanation
                            else [],
                        },
                    }
                    for dimension in match.dimensions
                ]
                explanations = _explanations(match.dimensions)
                await uow.matches.save_result(
                    result_id,
                    job_id,
                    profile_id,
                    version_id or None,
                    match.model_id,
                    match.model_version,
                    dict(match.weights),
                    input_hash,
                    match.fit_score,
                    match.dimension_coverage,
                    dimensions,
                    match.evaluated_at.isoformat() if match.evaluated_at else "",
                    explanations=explanations,
                )
                results.append(
                    {
                        "result_id": result_id,
                        "job_id": job_id,
                        "fit_score": match.fit_score,
                        "dimension_coverage": match.dimension_coverage,
                        "reused": False,
                    }
                )
                scored += 1
            await uow.commit()
        summary = {
            "available_jobs": available,
            "considered": len(jobs),
            "scored": scored,
            "reused": reused,
            "truncated": truncated,
        }
        return results, summary

    async def get_results(
        self,
        profile_id: str = "default",
        limit: int = 50,
        *,
        include_out_of_area: bool = False,
        include_missing_skills: bool = False,
    ) -> dict[str, Any]:
        async with self._container.uow_factory() as uow:
            rows = await uow.matches.list_results(profile_id, limit=limit)
            stored_weights = await uow.matches.get_weights(profile_id)
            weights = normalize_weights(dict(stored_weights or DEFAULT_WEIGHTS))
            overrides = await uow.resumes.get_overrides(profile_id)
            preferences = overrides.get("preferences")
            if not isinstance(preferences, dict):
                preferences = dict(overrides)
            profile = await uow.resumes.get_profile(profile_id)
            version_id = (
                str(profile.get("active_resume_version_id") or "") if profile else ""
            )
            skills = [
                str(row["keyword_id"])
                for row in await uow.resumes.get_skills(version_id)
            ] if version_id else []
            statuses = await uow.resumes.get_skill_status(profile_id)
            for key in normalize_skill_ids(overrides.get("skills_added")):
                if key not in skills:
                    skills.append(key)
            candidate = SimpleNamespace(skills=skills, skill_status=dict(statuses))
            saved_states = await uow.job_user_state.get_many(
                [str(row.get("job_id")) for row in rows]
            )
            results: list[dict[str, Any]] = []
            hidden_out_of_area = 0
            hidden_missing_skills = 0
            for row in rows:
                result = dict(row)
                result_id = str(result["id"])
                result["result_id"] = result_id
                result["weights"] = _json(result.pop("weights_json", None), {})
                result["dimensions"] = [
                    _dimension_row(dimension)
                    for dimension in await uow.matches.list_dimensions(result_id)
                ]
                result["explanations"] = [
                    _explanation_row(explanation)
                    for explanation in await uow.matches.list_explanations(result_id)
                ]
                job = await uow.jobs.get(str(result.get("job_id")))
                if job is None:
                    continue
                job = await ensure_requirements(uow, job)
                requirements = job.get("requirements")
                requirements = requirements if isinstance(requirements, dict) else {}
                coverage, missing = required_skill_coverage(
                    candidate, job.get("required_skills") or []
                )
                scope = requirements.get("location_scope")
                scope = scope if isinstance(scope, dict) else {}
                verdict = job_in_area(
                    location_text(job),
                    str(job.get("workplace_type") or "unknown"),
                    preferences,
                    requirements,
                )
                if verdict is False:
                    hidden_out_of_area += 1
                    if not include_out_of_area:
                        continue
                if coverage is not None and coverage < 0.5:
                    if not include_missing_skills:
                        hidden_missing_skills += 1
                        continue
                result.update(
                    {
                        "job_title": result.get("job_title") or job.get("title"),
                        "job_url": result.get("job_url")
                        or job.get("canonical_url"),
                        "location_text": location_text(job),
                        "workplace_type": job.get("workplace_type") or "unknown",
                        "required_skills": list(job.get("required_skills") or []),
                        "preferred_skills": list(job.get("preferred_skills") or []),
                        "missing_required_skills": missing,
                        "required_skill_coverage": coverage,
                        "location_scope": scope,
                        "saved": saved_states.get(str(result.get("job_id"))) == "saved",
                        "outside_area": verdict is False,
                        "hidden_for_missing_skills": (
                            coverage is not None and coverage < 0.5
                        ),
                    }
                )
                results.append(result)
            await uow.commit()
        return {
            "results": results,
            "weights": weights,
            "hidden_out_of_area": hidden_out_of_area,
            "hidden_missing_skills": hidden_missing_skills,
        }

    async def update_weights(
        self, profile_id: str, weights: dict[str, Any]
    ) -> dict[str, float]:
        normalized = normalize_weights(weights)
        async with self._container.uow_factory() as uow:
            stored = await uow.matches.get_weights(profile_id)
            previous = normalize_weights(dict(stored or DEFAULT_WEIGHTS))
            await uow.weight_versions.add(
                FitWeightVersionRecord(
                    id=f"fwt_{uuid.uuid4().hex[:16]}",
                    profile_id=profile_id,
                    weights=normalized,
                    previous_weights=previous if stored else None,
                    source="manual",
                    created_by="user",
                    created_at=self._container.clock.now(),
                    evidence={},
                )
            )
            await uow.matches.set_weights(profile_id, normalized)
            await uow.commit()
        return normalized


def _profile_hash(
    version: Mapping[str, Any] | None,
    overrides: Mapping[str, Any],
    statuses: Mapping[str, str],
    skills: list[str],
) -> str:
    payload = {
        "resume_version_id": version.get("id") if version else None,
        "text_hash": version.get("text_hash") if version else None,
        "overrides": overrides,
        "skill_status": statuses,
        "skills": sorted(skills),
    }
    return _hash(payload)


def _input_hash(
    job: dict[str, Any], profile_hash: str, weights: dict[str, float]
) -> str:
    snapshot = job.get("content_hash") or _hash(
        {
            "title": job.get("title"),
            "description": job.get("description_text"),
            "posted_at": job.get("posted_at"),
            "fetched_at": job.get("fetched_at"),
        }
    )
    return _hash(
        {
            "model": _MODEL.id,
            "model_version": _MODEL.version,
            "weights": weights,
            "profile": profile_hash,
            "job": snapshot,
        }
    )


def _hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parsed_json(value: object) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json(value: object, fallback: Any) -> Any:
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except ValueError:
            return fallback
    return fallback


def _titles(overrides: Mapping[str, Any], parsed: dict[str, Any]) -> list[str]:
    explicit = overrides.get("titles")
    if isinstance(explicit, list):
        return [str(title) for title in explicit]
    titles: list[str] = []
    for source in (overrides.get("experience"), parsed.get("experience")):
        if isinstance(source, list):
            for entry in source:
                if isinstance(entry, dict) and entry.get("title"):
                    titles.append(str(entry["title"]))
    return titles


def _explanations(dimensions: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for dimension in dimensions:
        if not dimension.available:
            rows.append(
                {
                    "reason_code": "dimension.unavailable",
                    "message": f"{dimension.dimension}: no data, excluded from the fit score",
                    "evidence": [],
                }
            )
        explanation = dimension.explanation
        if explanation is None:
            continue
        for reason in explanation.reasons:
            code = reason.code if "." in reason.code else f"{dimension.dimension}.{reason.code}"
            rows.append(
                {"reason_code": code, "message": reason.message, "evidence": []}
            )
    return rows


def _summary(row: Any, *, reused: bool) -> dict[str, Any]:
    data = dict(row)
    return {
        "result_id": data.get("id"),
        "job_id": data.get("job_id"),
        "fit_score": data.get("fit_score"),
        "dimension_coverage": data.get("dimension_coverage"),
        "reused": reused,
    }


def _dimension_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["available"] = bool(data.get("available"))
    data["details"] = _json(data.pop("details_json", None), {})
    return data


def _explanation_row(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["evidence"] = _json(data.pop("evidence_json", None), [])
    return {
        "reason_code": data.get("reason_code"),
        "message": data.get("message"),
        "evidence": data.get("evidence"),
    }
