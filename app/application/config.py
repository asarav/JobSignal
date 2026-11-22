"""Typed application configuration (spec §4.5 / §20).

All sections are strict: unknown keys are rejected so user overrides stored in
``app_settings`` fail loudly instead of being silently ignored.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

SETTINGS_KEY = "app_config"
SETTINGS_SCHEMA_VERSION = 1

# Paths/identity are fixed by the desktop supervisor for a run. They cannot be
# patched through settings because the worker database is already open.
IMMUTABLE_SETTING_PATHS = frozenset(
    {
        "paths.data_dir",
        "paths.db_name",
        "app_version",
        "protocol_version",
    }
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PathsConfig(StrictModel):
    data_dir: str = ""
    db_name: str = "lodestar.sqlite"


class HttpConfig(StrictModel):
    user_agent: str = "Lodestar/0.0.0 (+local-desktop)"
    timeout_s: float = 20.0
    #: Hard ceiling for any single request; a caller may ask for more than
    #: ``timeout_s`` (the slow public-index queries in discovery do) but never
    #: more than this.
    max_timeout_s: float = 120.0
    max_bytes: int = 5_000_000
    max_redirects: int = 5


class RetryConfig(StrictModel):
    max_attempts: int = 4
    base_s: float = 2.0
    cap_s: float = 300.0
    jitter: str = "full"


class ScheduleConfig(StrictModel):
    interval_hours: int = 12
    jitter_pct: int = 10


class CrawlConfig(StrictModel):
    global_concurrency: int = 8
    per_origin_concurrency: int = 2
    per_origin_rps: float = 1.0
    burst: int = 2
    retry: RetryConfig = Field(default_factory=RetryConfig)
    default_schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    not_seen_threshold: int = 2
    min_refetch_hours: int = 6
    block_backoff_hours: list[int] = Field(default_factory=lambda: [6, 24, 72, 168])


class ExtractionCompletenessConfig(StrictModel):
    require: list[str] = Field(default_factory=lambda: ["title", "description", "company"])
    one_of: list[str] = Field(default_factory=lambda: ["location", "workplace_type"])


class ExtractionConfig(StrictModel):
    order: list[str] = Field(
        default_factory=lambda: [
            "extract.public_endpoint",
            "extract.jsonld",
            "extract.embedded_json",
            "extract.static_html",
            "extract.rendered_dom",
            "extract.generic",
            "extract.partial",
        ]
    )
    completeness: ExtractionCompletenessConfig = Field(
        default_factory=ExtractionCompletenessConfig
    )


class DedupThresholdsConfig(StrictModel):
    reposted_identical: float = 0.90
    materially_updated: float = 0.75
    similar_distinct: float = 0.60


class DedupConfig(StrictModel):
    lookback_days: int = 365
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "title": 0.30,
            "location": 0.15,
            "description": 0.40,
            "department": 0.10,
            "salary": 0.05,
        }
    )
    thresholds: DedupThresholdsConfig = Field(default_factory=DedupThresholdsConfig)
    material_change_jaccard_delta: float = 0.10


class DiscoveryConfig(StrictModel):
    """Continuous discovery, funnel and budget settings (M4/M6).

    These keys used to be an untyped dict, so a typo (``max_new_source_per_run``)
    was silently ignored and the documented defaults applied instead.  They are
    validated now: a bad patch fails at save time with the offending key.
    """

    #: Pause between automatic passes.  Unset means the shipped hourly default
    #: (``AUTO_DISCOVER_INTERVAL_S``); the legacy hours key applies only when
    #: minutes is unset, so older settings keep working.
    auto_discover_interval_minutes: int | None = Field(default=None, gt=0)
    auto_discover_interval_hours: float | None = Field(default=None, gt=0)
    max_new_sources_per_run: int = Field(default=200, gt=0)
    max_crawl_sources_per_run: int = Field(default=60, gt=0)
    cursor_step: int = Field(default=12, gt=0)
    #: Ceiling on index slices walked per UTC day, shared by every pass.  A
    #: pass costs ``cursor_step`` slices, so the default (288) covers the
    #: hourly cadence (24 x 12).  It only bites when the cadence is shortened
    #: so far that continuous passes would hammer the public indexes.
    max_index_slices_per_day: int = Field(default=288, ge=0)
    web_index_limit: int = Field(default=200, gt=0)
    web_index_cdx_limit: int = Field(default=10_000, gt=0)
    provider_timeout_s: float = Field(default=300.0, gt=0)
    warmup_passes: int = Field(default=5, ge=0)
    funnel_enabled: bool = True
    funnel_postings_per_pass: int = Field(default=50, gt=0)
    tier_intervals_s: list[float] = Field(
        default_factory=lambda: [3600.0, 86400.0, 604800.0]
    )


class MatchingConfig(StrictModel):
    model: str = "fit.weighted_v1"
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "skills_resume": 0.35,
            "experience_seniority": 0.20,
            "responsibilities": 0.20,
            "location_workplace": 0.10,
            "compensation": 0.10,
            "freshness": 0.05,
        }
    )
    freshness_half_life_days: int = 14
    min_dimension_coverage_warning: float = 0.6


class RiskPriorConfig(StrictModel):
    risk: float = 0.20
    weight: float = 0.15


class PostingIntelligenceConfig(StrictModel):
    model: str = "risk.heuristic_v1"
    prior: RiskPriorConfig = Field(default_factory=RiskPriorConfig)
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "posting_age": 0.10,
            "repost_frequency": 0.20,
            "endpoint_availability": 0.25,
            "content_staleness": 0.10,
            "duplicate_consistency": 0.10,
            "company_hiring_activity": 0.15,
            "explicit_closure_language": 0.30,
            "implausible_requirements": 0.05,
        }
    )
    thresholds: dict[str, Any] = Field(
        default_factory=lambda: {
            "likely_inactive": {"risk_min": 0.75, "confidence_min": 0.50},
            "likely_stale": {
                "risk_min": 0.50,
                "confidence_min": 0.40,
                "alt": {"risk_min": 0.75, "confidence_min": 0.30},
            },
            "likely_active": {"risk_max": 0.30, "confidence_min": 0.35},
        }
    )
    endpoint_check: dict[str, int] = Field(
        default_factory=lambda: {
            "interval_days": 7,
            "obs_max_age_days": 14,
            "transient_failures_required": 2,
        }
    )
    company_tiers: dict[str, int] = Field(
        default_factory=lambda: {
            "insufficient_lt_jobs": 3,
            "insufficient_lt_days": 21,
            "adequate_min_jobs": 8,
            "adequate_min_days": 60,
            "min_applications_for_rates": 5,
        }
    )
    calibration: dict[str, int] = Field(
        default_factory=lambda: {"min_outcomes_for_proposals": 30, "min_bucket_n": 5}
    )


class SuppressionConfig(StrictModel):
    exact_repost_suppress: bool = True
    similar_role_cooldown_days: int = 180
    similar_role_threshold: float = 0.75
    company_wide_cooldown_enabled: bool = False
    company_wide_cooldown_days: int = 90


class RankingConfig(StrictModel):
    credibility_policy: str = "deprioritize"
    deprioritize_factor: float = 0.5


class AutofillConfig(StrictModel):
    min_field_confidence: float = 0.85
    allow_step_navigation: bool = False
    submit_lexicon: list[str] = Field(
        default_factory=lambda: [
            "submit",
            "apply",
            "apply now",
            "send application",
            "finish",
            "complete",
            "confirm",
            "done",
        ]
    )


class LoggingConfig(StrictModel):
    level: str = "info"
    rotate_files: int = 10
    rotate_mb: int = 5


class FeatureFlags(StrictModel):
    search_engine_discovery: bool = False
    rendered_dom_extraction: bool = True
    local_embeddings: bool = False
    autofill: bool = True
    outcome_analytics: bool = True
    external_plugins: bool = False


class PluginsConfig(StrictModel):
    enabled: dict[str, bool] = Field(default_factory=dict)
    call_timeout_s: float = 30.0


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LODESTAR_", extra="ignore")

    app_version: str = "0.0.0"
    protocol_version: int = 1
    paths: PathsConfig = Field(default_factory=PathsConfig)
    http: HttpConfig = Field(default_factory=HttpConfig)
    crawl: CrawlConfig = Field(default_factory=CrawlConfig)
    discovery: DiscoveryConfig = Field(default_factory=DiscoveryConfig)
    extraction: ExtractionConfig = Field(default_factory=ExtractionConfig)
    dedup: DedupConfig = Field(default_factory=DedupConfig)
    matching: MatchingConfig = Field(default_factory=MatchingConfig)
    posting_intelligence: PostingIntelligenceConfig = Field(
        default_factory=PostingIntelligenceConfig
    )
    suppression: SuppressionConfig = Field(default_factory=SuppressionConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    autofill: AutofillConfig = Field(default_factory=AutofillConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    features: FeatureFlags = Field(default_factory=FeatureFlags)
    plugins: PluginsConfig = Field(default_factory=PluginsConfig)

    def with_data_dir(self, data_dir: str) -> AppConfig:
        return self.model_copy(
            update={"paths": self.paths.model_copy(update={"data_dir": data_dir})}
        )

    def effective_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def apply_patch(self, patch: Mapping[str, Any]) -> AppConfig:
        """Return a new validated config with ``patch`` deep-merged over this one."""
        _reject_immutable(patch)
        merged = deep_merge(self.effective_dict(), patch)
        try:
            return AppConfig.model_validate(merged)
        except ValidationError:
            raise


def discovery_settings(config: object) -> dict[str, Any]:
    """Discovery settings as a plain mapping, whether typed or legacy.

    Callers (the pass, the worker loop, the provider factory) all want
    ``Mapping.get`` semantics; the config now holds a validated model, but a
    dict is still accepted so tests and older call sites keep working.
    """
    settings = getattr(config, "discovery", None)
    if isinstance(settings, DiscoveryConfig):
        return settings.model_dump()
    if isinstance(settings, Mapping):
        return {str(key): value for key, value in settings.items()}
    return {}


def deep_merge(base: Mapping[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {k: _copy(v) for k, v in base.items()}
    for key, value in patch.items():
        current = result.get(key)
        if isinstance(value, Mapping) and isinstance(current, Mapping):
            result[key] = deep_merge(current, value)
        else:
            result[key] = _copy(value)
    return result


def _copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): _copy(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_copy(v) for v in value]
    return value


def _reject_immutable(patch: Mapping[str, Any]) -> None:
    for path in IMMUTABLE_SETTING_PATHS:
        node: Any = patch
        for part in path.split("."):
            if not isinstance(node, Mapping) or part not in node:
                break
            node = node[part]
        else:
            raise ValueError(f"setting {path!r} is fixed for this run and cannot be changed")


def validate_patch(patch: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a standalone settings patch against the default config."""
    AppConfig().apply_patch(patch)
    return cast(dict[str, Any], _copy(patch))
