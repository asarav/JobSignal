"""Posting-intelligence evaluation pipeline (M8, spec §9.2-§9.7).

This module is the only place that turns stored history into an assessment. It
is deliberately boring: assemble ``JobHistory`` from repositories, run the pure
providers, hand their signals to the pure model, and append the result. No
network, no clock of its own beyond the injected one, no policy in the UI.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from statistics import median
from typing import Any

from app.application.container import Container
from app.application.keywords import KeywordPersistenceService
from app.domain.models.common import PortError
from app.domain.models.enums import RiskClass, SignalStatus
from app.domain.models.posting import (
    ClassificationChangeRecord,
    CohortStats,
    CompanySummaryRecord,
    CompanySummaryView,
    EndpointPoint,
    JobHistory,
    ObservationPoint,
    RelatedJob,
    RepostPoint,
    RiskAssessmentRecord,
    RiskModelConfig,
    RiskThresholds,
    SignalContext,
    SignalObservationRecord,
    SignalResult,
    SnapshotPoint,
)
from app.domain.services.company_resolution import canonical_map
from app.domain.services.risk import MODEL_ID, MODEL_VERSION, HeuristicRiskModel
from app.domain.services.signals import DEFAULT_SIGNAL_PROVIDERS

_MODEL = HeuristicRiskModel()
_PROVIDERS = DEFAULT_SIGNAL_PROVIDERS

#: Bumped when the company-summary recipe changes (spec §9.7).
COMPANY_METHODOLOGY_VERSION = "company_summary.v1"

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9.+#-]*")
_DAY = 86_400.0


def _dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value is not None else None


def _json_load(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except ValueError:
        return default


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.casefold())[:2000])


def _jaccard(left: str, right: str) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _config_from_app(app_config: Any) -> RiskModelConfig:
    pi = getattr(app_config, "posting_intelligence", None)
    if pi is None:  # pragma: no cover - AppConfig always carries it
        return RiskModelConfig(model_id=MODEL_ID, model_version=MODEL_VERSION)
    thresholds = getattr(pi, "thresholds", {}) or {}
    inactive = thresholds.get("likely_inactive", {})
    stale = thresholds.get("likely_stale", {})
    alt = stale.get("alt", {})
    active = thresholds.get("likely_active", {})
    return RiskModelConfig(
        model_id=str(getattr(pi, "model", MODEL_ID)),
        model_version=MODEL_VERSION,
        prior_risk=float(pi.prior.risk),
        prior_weight=float(pi.prior.weight),
        weights={str(key): float(value) for key, value in (pi.weights or {}).items()},
        thresholds=RiskThresholds(
            inactive_risk_min=float(inactive.get("risk_min", 0.75)),
            inactive_confidence_min=float(inactive.get("confidence_min", 0.50)),
            stale_risk_min=float(stale.get("risk_min", 0.50)),
            stale_confidence_min=float(stale.get("confidence_min", 0.40)),
            stale_alt_risk_min=float(alt.get("risk_min", 0.75)),
            stale_alt_confidence_min=float(alt.get("confidence_min", 0.30)),
            active_risk_max=float(active.get("risk_max", 0.30)),
            active_confidence_min=float(active.get("confidence_min", 0.35)),
        ),
    )


def config_payload(cfg: RiskModelConfig) -> dict[str, Any]:
    """The exact inputs an assessment depends on, for ``risk_model_versions``."""
    return {
        "model_id": cfg.model_id,
        "model_version": cfg.model_version,
        "prior_risk": cfg.prior_risk,
        "prior_weight": cfg.prior_weight,
        "weights": dict(sorted(cfg.weights.items())),
        "thresholds": asdict(cfg.thresholds),
    }


def config_from_payload(payload: Mapping[str, Any]) -> RiskModelConfig:
    try:
        thresholds = RiskThresholds(**dict(payload.get("thresholds") or {}))
        return RiskModelConfig(
            model_id=str(payload["model_id"]),
            model_version=str(payload["model_version"]),
            prior_risk=float(payload.get("prior_risk", 0.20)),
            prior_weight=float(payload.get("prior_weight", 0.15)),
            weights={
                str(key): float(value)
                for key, value in dict(payload.get("weights") or {}).items()
            },
            thresholds=thresholds,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise PortError(
            code="validation.invalid_params",
            message="risk model configuration is invalid",
            retryable=False,
            details={"error": str(exc)},
        ) from exc


def _merge_config(current: RiskModelConfig, patch: Mapping[str, Any]) -> RiskModelConfig:
    payload = config_payload(current)
    for key in ("prior_risk", "prior_weight"):
        if key in patch:
            payload[key] = patch[key]
    if "weights" in patch:
        weights = dict(payload["weights"])
        weights.update({str(k): v for k, v in dict(patch["weights"]).items()})
        payload["weights"] = weights
    if "thresholds" in patch:
        thresholds = dict(payload["thresholds"])
        thresholds.update(dict(patch["thresholds"]))
        payload["thresholds"] = thresholds
    unknown = set(patch) - {"prior_risk", "prior_weight", "weights", "thresholds"}
    if unknown:
        raise PortError(
            code="validation.invalid_params",
            message=f"unknown risk configuration keys: {sorted(unknown)}",
            retryable=False,
        )
    return config_from_payload(payload)


class RiskEvaluationService:
    """Evaluates a bounded set of jobs and appends reproducible assessments."""

    def __init__(self, container: Container) -> None:
        self._container = container

    async def evaluate(
        self,
        *,
        job_ids: Sequence[str] | None = None,
        scope: str = "active",
        force: bool = False,
        refresh_endpoints: bool = False,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if scope not in {"active", "all"}:
            raise PortError(
                code="validation.invalid_params",
                message="scope must be 'active' or 'all'",
                retryable=False,
            )
        config = await self.current_config()
        await self._seed_registry()
        targets = await self._targets(job_ids, scope, limit)
        if refresh_endpoints and targets:
            from app.application.endpoint_verifier import EndpointVerifier

            await EndpointVerifier(self._container).verify(
                job_ids=targets, force=True
            )
        evaluated: list[str] = []
        skipped = 0
        for job_id in targets:
            outcome = await self._evaluate_one(
                job_id, config, force=force, refresh_endpoints=refresh_endpoints
            )
            if outcome is None:
                skipped += 1
            else:
                evaluated.append(outcome)
        if evaluated:
            self._container.events.publish(
                "posting_intelligence.updated", {"job_ids": evaluated}
            )
        return {"evaluated": len(evaluated), "skipped": skipped, "job_ids": evaluated}

    async def current_config(self) -> RiskModelConfig:
        default = _config_from_app(self._container.config)
        async with self._container.uow_factory() as uow:
            row = await uow.intelligence.model_versions.latest(default.model_id)
            await uow.commit()
        if row is None:
            return default
        try:
            return config_from_payload(json.loads(str(row["config_json"])))
        except (ValueError, PortError):
            return default

    async def configure(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        current = await self.current_config()
        merged = _merge_config(current, patch)
        async with self._container.uow_factory() as uow:
            row_id = await uow.intelligence.model_versions.ensure(
                model_id=merged.model_id,
                model_version=merged.model_version,
                config_json=json.dumps(config_payload(merged), sort_keys=True),
                config_hash=merged.fingerprint(),
                description="User-adjusted posting-intelligence weights",
            )
            await uow.commit()
        return {
            "model_version_id": row_id,
            "model_id": merged.model_id,
            "model_version": merged.model_version,
            "config_hash": merged.fingerprint(),
            "config": config_payload(merged),
        }

    async def _seed_registry(self) -> None:
        entries = [
            {
                "signal_id": provider.id,
                "description": provider.description,
                "provider_version": provider.version,
                "default_weight": provider.default_weight,
            }
            for provider in _PROVIDERS
        ]
        async with self._container.uow_factory() as uow:
            await uow.intelligence.signals.upsert_registry(entries)
            await uow.commit()

    async def _targets(
        self, job_ids: Sequence[str] | None, scope: str, limit: int | None = None
    ) -> list[str]:
        if job_ids:
            return [str(job_id) for job_id in job_ids if job_id]
        targets: list[str] = []
        page = 200
        offset = 0
        while True:
            async with self._container.uow_factory() as uow:
                rows = await uow.jobs.list(limit=page, offset=offset)
                await uow.commit()
            if not rows:
                break
            for row in rows:
                status = str(row.get("posting_status") or "active")
                if scope == "all" or status == "active":
                    targets.append(str(row["id"]))
                    if limit is not None and len(targets) >= limit:
                        return targets
            if len(rows) < page:
                break
            offset += page
        return targets

    async def _evaluate_one(
        self,
        job_id: str,
        config: RiskModelConfig,
        *,
        force: bool,
        refresh_endpoints: bool,
    ) -> str | None:
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            job = await uow.jobs.get(job_id)
            if job is None:
                raise PortError(
                    code="not_found",
                    message=f"unknown job: {job_id}",
                    retryable=False,
                )
            # Keywords are stored per posting revision by the same pass that
            # assesses risk, so prevalence fills without a manual call.
            await KeywordPersistenceService(self._container).persist_for_job(
                uow, job, now
            )
            history = await self._history(uow, job, now)
            context = self._signal_context(job_id, now)
            signals = [
                provider.evaluate(history, context) for provider in _PROVIDERS
            ]
            assessment = _MODEL.assess(signals, config, now)
            input_hash = self._input_hash(job, history, config, now)
            existing = await uow.intelligence.assessments.find_by_input_hash(
                job_id, input_hash
            )
            if existing is not None and not force:
                await uow.commit()
                return None

            previous = await uow.intelligence.assessments.latest(job_id)
            model_version_id = await uow.intelligence.model_versions.ensure(
                model_id=config.model_id,
                model_version=config.model_version,
                config_json=json.dumps(config_payload(config), sort_keys=True),
                config_hash=config.fingerprint(),
                description="Default posting-intelligence configuration",
            )
            signal_ids: list[str] = []
            for signal in signals:
                signal_id = f"sig_{uuid.uuid4().hex[:16]}"
                signal_ids.append(signal_id)
                await uow.intelligence.signals.add(
                    SignalObservationRecord(
                        id=signal_id,
                        job_id=job_id,
                        signal=signal,
                        evaluated_at=now,
                    )
                )
            assessment_id = f"ra_{uuid.uuid4().hex[:16]}"
            await uow.intelligence.assessments.add(
                RiskAssessmentRecord(
                    id=assessment_id,
                    job_id=job_id,
                    assessment=assessment,
                    model_version_id=model_version_id,
                    signal_observation_ids=signal_ids,
                    input_hash=input_hash,
                )
            )
            previous_class = str(previous["class"]) if previous else None
            if previous is not None and previous_class != assessment.risk_class.value:
                await uow.intelligence.classification_changes.add(
                    ClassificationChangeRecord(
                        id=f"cc_{uuid.uuid4().hex[:16]}",
                        job_id=job_id,
                        from_class=previous_class,
                        to_class=assessment.risk_class.value,
                        from_assessment_id=str(previous["id"]),
                        to_assessment_id=assessment_id,
                        changed_at=now,
                    )
                )
            await uow.commit()
        return job_id

    def _signal_context(self, job_id: str, now: datetime) -> SignalContext:
        pi = getattr(self._container.config, "posting_intelligence", None)
        endpoint_cfg = dict(getattr(pi, "endpoint_check", {}) or {})
        tiers = dict(getattr(pi, "company_tiers", {}) or {})
        return SignalContext(
            job_id=job_id,
            now=now,
            endpoint_obs_max_age_days=int(endpoint_cfg.get("obs_max_age_days", 14)),
            transient_failures_required=int(
                endpoint_cfg.get("transient_failures_required", 2)
            ),
            cohort_min_sample=int(tiers.get("adequate_min_jobs", 8)) + 7,
            company_min_sample=int(tiers.get("adequate_min_jobs", 8)),
        )

    async def _history(self, uow: Any, job: Mapping[str, Any], now: datetime) -> JobHistory:
        job_id = str(job["id"])
        snapshot_rows = await uow.snapshots.list_for_job(job_id)
        snapshots: list[SnapshotPoint] = []
        for row in reversed(list(snapshot_rows)):
            payload = _json_load(
                row.get("snapshot_json", row.get("snapshot")), {}
            )
            if not isinstance(payload, Mapping):
                payload = {}
            snapshots.append(
                SnapshotPoint(
                    at=_dt(row.get("created_at")) or now,
                    content_hash=str(row.get("content_hash") or ""),
                    title=str(payload.get("title") or ""),
                    description_text=str(payload.get("description_text") or ""),
                )
            )

        observation_rows = await uow.observations.list_for_job(job_id)
        observations: list[ObservationPoint] = []
        days: set[str] = set()
        for row in observation_rows:
            at = _dt(row.get("observed_at")) or now
            days.add(at.strftime("%Y-%m-%d"))
            observations.append(
                ObservationPoint(
                    at=at,
                    kind=str(row.get("posting_status") or "seen"),
                    payload=_json_load(row.get("details_json", row.get("details")), {}),
                )
            )

        repost_rows = await uow.dedup.list_repost_events(job_id)
        reposts = [
            RepostPoint(
                at=_dt(row.get("detected_at")) or now,
                kind=str(row.get("class") or "reposted_identical"),
                similarity=float(row.get("confidence") or 0.0),
            )
            for row in repost_rows
        ]

        endpoint_rows = await uow.intelligence.endpoints.list_for_job(job_id)
        endpoints = [
            EndpointPoint(
                at=_dt(row.get("observed_at")) or now,
                state=str(row.get("state") or "error"),
                http_status=row.get("http_status"),
                final_url=row.get("final_url"),
            )
            for row in endpoint_rows
        ]

        related = await self._related_jobs(uow, job, job_id)

        company_id = job.get("company_id")
        summary_row = (
            await uow.intelligence.company_summaries.latest(str(company_id))
            if company_id
            else None
        )
        view = _company_view(summary_row)
        cohort = None
        metrics = dict(summary_row.get("metrics") or {}) if summary_row else {}
        if (
            summary_row is not None
            and str(summary_row.get("sample_tier")) == "adequate"
            and metrics.get("median_posting_duration_days")
        ):
            cohort = CohortStats(
                median_days=float(metrics["median_posting_duration_days"]),
                sample_size=int(metrics.get("n_unique_postings") or 0),
                scope="company",
            )

        latest_snapshot = snapshots[-1] if snapshots else None
        description = str(job.get("description_text") or "")
        return JobHistory(
            job_id=job_id,
            first_seen_at=_dt(job.get("first_seen_at")),
            last_seen_at=_dt(job.get("last_seen_at")),
            snapshot_count=len(snapshots),
            observations=observations,
            snapshots=snapshots,
            reposts=reposts,
            endpoints=endpoints,
            related_jobs=related,
            company_summary=view,
            cohort=cohort,
            statuses_by_source={
                str(job["source_id"]): str(job.get("posting_status") or "active")
            }
            if job.get("source_id")
            else {},
            field_conflicts=0,
            observed_crawl_cycles=len(days) or None,
            page_text=str(
                (latest_snapshot.description_text if latest_snapshot else "")
                or description
            ),
            title=str(job.get("title") or ""),
            description_text=str(
                (latest_snapshot.description_text if latest_snapshot else "")
                or description
            ),
            requirements_text="",
        )

    async def _related_jobs(
        self, uow: Any, job: Mapping[str, Any], job_id: str
    ) -> list[RelatedJob]:
        company_id = job.get("company_id")
        if not company_id:
            return []
        relations = await uow.jobs.list_relations(job_id)
        related_ids = [
            str(row.get("related_job_id"))
            for row in relations
            if str(row.get("relation")) != "user_unmerged"
            and str(row.get("related_job_id")) != job_id
        ]
        company_jobs = await uow.jobs.list(company_id=str(company_id), limit=100)
        candidates = await uow.jobs.list_by_ids(related_ids)
        seen = {job_id}
        out: list[RelatedJob] = []
        this_text = str(job.get("description_text") or "")
        for row in list(candidates) + list(company_jobs):
            other_id = str(row.get("id"))
            if not other_id or other_id in seen:
                continue
            seen.add(other_id)
            out.append(
                RelatedJob(
                    job_id=other_id,
                    description_similarity=_jaccard(
                        this_text, str(row.get("description_text") or "")
                    ),
                )
            )
        return out[:50]

    @staticmethod
    def _input_hash(
        job: Mapping[str, Any],
        history: JobHistory,
        config: RiskModelConfig,
        now: datetime,
    ) -> str:
        summary = history.company_summary
        payload = {
            "content_hash": job.get("content_hash"),
            "first_seen_at": job.get("first_seen_at"),
            "last_seen_at": job.get("last_seen_at"),
            "snapshots": [point.content_hash for point in history.snapshots],
            "endpoints": [
                f"{point.at.isoformat()}:{point.state}" for point in history.endpoints
            ],
            "reposts": [f"{point.at.isoformat()}:{point.kind}" for point in history.reposts],
            "company_summary": (
                {
                    "tier": summary.sample_tier,
                    "n": summary.n_unique,
                    "window": round(summary.window_days, 1),
                    "repost_ratio": round(summary.repost_ratio, 3),
                    "inactive_ratio": round(summary.inactive_ratio, 3),
                }
                if summary is not None
                else None
            ),
            "observations": len(history.observations),
            "config": config.fingerprint(),
            "day": now.strftime("%Y-%m-%d"),
        }
        encoded = json.dumps(payload, sort_keys=True, default=str).encode()
        return hashlib.sha256(encoded).hexdigest()[:24]


def _company_view(row: Mapping[str, Any] | None) -> CompanySummaryView | None:
    if row is None:
        return None
    metrics = dict(row.get("metrics") or {})
    return CompanySummaryView(
        sample_tier=str(row.get("sample_tier") or "insufficient"),
        n_unique=int(metrics.get("n_unique_postings") or 0),
        window_days=float(metrics.get("observation_window_days") or 0.0),
        repost_ratio=float(metrics.get("repost_ratio") or 0.0),
        inactive_ratio=float(metrics.get("inactive_ratio") or 0.0),
        median_duration_days=(
            float(metrics["median_posting_duration_days"])
            if metrics.get("median_posting_duration_days") is not None
            else None
        ),
    )


class CompanySummaryService:
    """Company-level hiring intelligence with sample-size gates (spec §9.7)."""

    def __init__(self, container: Container) -> None:
        self._container = container

    async def compute(
        self, company_ids: Sequence[str], *, force: bool = False
    ) -> int:
        now = self._container.clock.now()
        day = now.strftime("%Y-%m-%d")
        written = 0
        for company_id in company_ids:
            async with self._container.uow_factory() as uow:
                merged = canonical_map(await uow.company_merges.latest_actions())
                canonical = merged.get(str(company_id), str(company_id))
                if canonical != str(company_id):
                    # The survivor carries the summary; a merged child never does.
                    await uow.commit()
                    continue
                existing = await uow.intelligence.company_summaries.for_day(
                    str(company_id), day
                )
                if existing is not None and not force:
                    continue
                record = await self._build(uow, str(company_id), now)
                await uow.intelligence.company_summaries.add(record)
                await uow.commit()
            written += 1
        return written

    async def _build(
        self, uow: Any, company_id: str, now: datetime
    ) -> CompanySummaryRecord:
        jobs = list(await uow.jobs.list(company_id=company_id, limit=500))
        job_ids = [str(job["id"]) for job in jobs]
        assessments = await uow.intelligence.assessments.latest_for_jobs(job_ids)
        endpoints = await uow.intelligence.endpoints.latest_for_jobs(job_ids)

        first_seen = [_dt(job.get("first_seen_at")) for job in jobs]
        last_seen = [_dt(job.get("last_seen_at")) for job in jobs]
        starts = [value for value in first_seen if value is not None]
        ends = [value for value in last_seen if value is not None]
        window_days = (
            max(0.0, (max(ends) - min(starts)).total_seconds() / _DAY)
            if starts and ends
            else 0.0
        )

        reposted = 0
        no_change = 0
        durations: list[float] = []
        removed = 0
        for job in jobs:
            job_id = str(job["id"])
            events = await uow.dedup.list_repost_events(job_id)
            if events:
                reposted += 1
            if str(job.get("posting_status")) == "removed":
                removed += 1
                start, end = _dt(job.get("first_seen_at")), _dt(job.get("last_seen_at"))
                if start is not None and end is not None:
                    durations.append(max(0.0, (end - start).total_seconds() / _DAY))
        for job_id in job_ids[:200]:
            snapshots = await uow.snapshots.list_for_job(job_id)
            if len(snapshots) < 2:
                no_change += 1

        inaccessible = sum(
            1
            for row in endpoints.values()
            if str(row.get("state"))
            in {"gone_404_410", "closed_message", "form_unavailable"}
        )
        inactive = sum(
            1
            for row in assessments.values()
            if str(row.get("class")) in {"likely_stale", "likely_inactive"}
            and float(row.get("confidence") or 0.0) >= 0.5
        )
        applied = list(await uow.applications.list_applied(company_id=company_id))

        n_unique = len(jobs)
        metrics: dict[str, Any] = {
            "n_unique_postings": n_unique,
            "observation_window_days": round(window_days, 1),
            "n_reposted_positions": reposted,
            "repost_ratio": round(reposted / n_unique, 3) if n_unique else 0.0,
            "n_removed": removed,
            "removal_rate": round(removed / n_unique, 3) if n_unique else 0.0,
            "median_posting_duration_days": (
                round(median(durations), 1) if durations else None
            ),
            "n_inaccessible_endpoints": inaccessible,
            "n_no_change_roles": no_change,
            "n_inactive_assessed": inactive,
            "inactive_ratio": round(inactive / n_unique, 3) if n_unique else 0.0,
            "n_applications": len(applied),
        }
        tier = _sample_tier(metrics, self._container.config)
        labels: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        if tier == "adequate":
            if metrics["repost_ratio"] >= 0.30 and reposted >= 3:
                labels.append(
                    {
                        "label": "Repeated reposting observed",
                        "code": "repeated_reposting",
                    }
                )
                evidence.append(
                    {
                        "kind": "reposts",
                        "description": f"{reposted} of {n_unique} roles were re-advertised",
                    }
                )
            if metrics["inactive_ratio"] >= 0.40 and inactive:
                labels.append(
                    {"label": "Elevated posting risk", "code": "elevated_posting_risk"}
                )
                evidence.append(
                    {
                        "kind": "assessments",
                        "description": (
                            f"{inactive} of {n_unique} roles are assessed as possibly "
                            "stale or likely inactive"
                        ),
                    }
                )
        return CompanySummaryRecord(
            id=f"chs_{uuid.uuid4().hex[:16]}",
            company_id=company_id,
            computed_at=now,
            sample_tier=tier,
            metrics=metrics,
            labels=labels,
            evidence=evidence,
            methodology_version=COMPANY_METHODOLOGY_VERSION,
        )


def _sample_tier(metrics: Mapping[str, Any], app_config: Any) -> str:
    tiers = dict(
        getattr(
            getattr(app_config, "posting_intelligence", None), "company_tiers", {}
        )
        or {}
    )
    n = int(metrics.get("n_unique_postings") or 0)
    window = float(metrics.get("observation_window_days") or 0.0)
    if n < int(tiers.get("insufficient_lt_jobs", 3)) or window < float(
        tiers.get("insufficient_lt_days", 21)
    ):
        return "insufficient"
    if n >= int(tiers.get("adequate_min_jobs", 8)) and window >= float(
        tiers.get("adequate_min_days", 60)
    ):
        return "adequate"
    return "limited"


class PostingIntelligenceUseCase:
    """IPC-facing read/act surface for posting intelligence."""

    def __init__(self, container: Container) -> None:
        self._container = container

    async def get_signals(self, job_id: str) -> dict[str, Any]:
        async with self._container.uow_factory() as uow:
            latest = await uow.intelligence.assessments.latest(job_id)
            if latest is None:
                await uow.commit()
                return {"job_id": job_id, "assessment": None, "signals": []}
            raw_ids = latest.get("signal_observation_ids")
            signal_ids = [
                str(value) for value in (raw_ids if isinstance(raw_ids, list) else [])
            ]
            rows = await uow.intelligence.signals.list_by_ids(signal_ids)
            await uow.commit()
        order = {signal_id: index for index, signal_id in enumerate(signal_ids)}
        signals = sorted(rows, key=lambda row: order.get(str(row["id"]), 999))
        return {
            "job_id": job_id,
            "assessment": dict(latest),
            "signals": [dict(row) for row in signals],
        }

    async def get_risk_history(self, job_id: str) -> dict[str, Any]:
        async with self._container.uow_factory() as uow:
            assessments = await uow.intelligence.assessments.list_for_job(job_id)
            changes = await uow.intelligence.classification_changes.list_for_job(
                job_id
            )
            await uow.commit()
        return {
            "job_id": job_id,
            "assessments": [dict(row) for row in assessments],
            "changes": [dict(row) for row in changes],
        }

    async def get_company_assessment(self, company_id: str) -> dict[str, Any]:
        async with self._container.uow_factory() as uow:
            summary = await uow.intelligence.company_summaries.latest(company_id)
            await uow.commit()
        return {"company_id": company_id, "summary": dict(summary) if summary else None}

    async def get_calibration(self) -> dict[str, Any]:
        from app.application.analytics import CalibrationService

        return await CalibrationService(self._container).get_calibration()

    async def configure_model(self, patch: Mapping[str, Any]) -> dict[str, Any]:
        return await RiskEvaluationService(self._container).configure(patch)

    async def evaluate_now(
        self,
        *,
        job_ids: Sequence[str] | None = None,
        scope: str = "active",
        force: bool = False,
        refresh_endpoints: bool = False,
    ) -> dict[str, Any]:
        return await RiskEvaluationService(self._container).evaluate(
            job_ids=job_ids,
            scope=scope,
            force=force,
            refresh_endpoints=refresh_endpoints,
        )


def assessment_risk_class(value: str) -> RiskClass:
    try:
        return RiskClass(value)
    except ValueError as exc:  # pragma: no cover - stored rows are constrained
        raise PortError(
            code="internal.unexpected",
            message=f"unknown stored risk class: {value!r}",
            retryable=False,
        ) from exc


def signal_from_row(row: Mapping[str, Any]) -> SignalResult:
    """Rehydrate a stored signal row, for the reproducibility check (§9.6)."""
    return SignalResult(
        signal_id=str(row["signal_id"]),
        provider_version=str(row.get("provider_version") or ""),
        status=SignalStatus(str(row.get("status") or "missing")),
        risk_contribution=(
            float(row["risk_contribution"])
            if row.get("risk_contribution") is not None
            else None
        ),
        confidence=float(row.get("confidence") or 0.0),
        strong=bool(row.get("strong")),
        evidence=(),
        missing_reason=row.get("missing_reason"),
        explanation=str(row.get("explanation") or ""),
        evidence_source=str(row.get("evidence_source") or ""),
        observed_at=_dt(row.get("observed_at")),
        evaluated_at=_dt(row.get("evaluated_at")),
    )


def days_between(start: datetime, end: datetime) -> float:
    return max(0.0, (end - start).total_seconds() / _DAY)


def wilson_interval(successes: int, total: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson interval, used by company and outcome statistics."""
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    spread = (
        z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    )
    return (max(0.0, centre - spread), min(1.0, centre + spread))


__all__ = [
    "COMPANY_METHODOLOGY_VERSION",
    "CompanySummaryService",
    "PostingIntelligenceUseCase",
    "RiskEvaluationService",
    "assessment_risk_class",
    "config_from_payload",
    "config_payload",
    "days_between",
    "signal_from_row",
    "wilson_interval",
]
