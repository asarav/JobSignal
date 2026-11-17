"""Test-only toy plugins for registry smoke tests and swapability (M0)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.plugins.registry import PluginDeps, PluginRegistry


class ToyAtsConnector:
    id = "ats.toy"
    version = "1.0.0"
    capabilities = frozenset({"list_jobs", "fetch_job"})

    def matches(self, url: str, sniff=None):
        from app.domain.models.source import MatchResult

        return MatchResult(matched="toy.example.com" in url, confidence=0.9)

    async def list_jobs(self, source, ctx):

        async def gen():
            from app.domain.models.job import RawJobRef

            yield RawJobRef(source_id=source.id, url="https://toy.example.com/jobs/1")

        return gen()

    async def fetch_job(self, ref, source, ctx):
        from app.domain.models.common import Ok
        from app.domain.models.job import RawJob

        return Ok(RawJob(ref=ref, title="Toy Job", body_text="toy description", body_html=None))


class ToySignalProvider:
    id = "signal.toy"
    version = "1.0.0"
    description = "Toy signal provider for swapability tests"
    default_weight = 0.10

    def evaluate(self, history, ctx):
        from app.domain.models.enums import SignalStatus
        from app.domain.models.posting import SignalResult

        return SignalResult(
            signal_id=self.id,
            provider_version=self.version,
            status=SignalStatus.AVAILABLE,
            risk_contribution=0.5,
            confidence=0.8,
            evidence=[],
            explanation="toy signal",
        )


class ConstantScoringModel:
    id = "match.constant"
    version = "1.0.0"

    def __init__(self, score: float = 0.5) -> None:
        self.score = score
        self.recorded_model_id: str | None = None

    async def evaluate(self, profile, job, prefs, strategies, cfg):
        self.recorded_model_id = cfg.model_id
        from app.domain.models.common import Explanation
        from app.domain.models.matching import MatchScore

        return MatchScore(
            model_id=cfg.model_id,
            model_version=cfg.model_version,
            fit_score=self.score,
            dimension_coverage=1.0,
            dimensions=[],
            explanation=Explanation(summary="constant", reasons=[]),
            weights={},
            evaluated_at=None,
        )


class ToyExtractionStrategy:
    id = "extract.toy"
    version = "1.0.0"
    default_order = 99

    def can_attempt(self, target) -> bool:
        return True

    async def extract(self, target, ctx):
        from app.domain.models.common import Ok
        from app.domain.models.extraction import ExtractionResult
        from app.domain.models.job import RawJob, RawJobRef

        ref = RawJobRef(source_id=target.source_id, url=target.url)
        raw = RawJob(ref=ref, title="toy", body_text="toy body", body_html=None)
        result = ExtractionResult(
            raw=raw,
            fields_found=("title",),
            confidence=0.9,
            strategy_id=self.id,
            plugin_version=self.version,
        )
        return Ok(result)


def register(registry: PluginRegistry, deps: PluginDeps) -> None:
    from app.domain.ports.plugins import PluginManifest

    registry.add(
        PluginManifest(
            id="ats.toy",
            kind="connector",
            version="1.0.0",
            api_version="1",
            capabilities=frozenset({"list_jobs", "fetch_job"}),
        ),
        ToyAtsConnector(),
    )
    registry.add(
        PluginManifest(
            id="signal.toy",
            kind="posting_signal",
            version="1.0.0",
            api_version="1",
            capabilities=frozenset({"evaluate"}),
        ),
        ToySignalProvider(),
    )
    registry.add(
        PluginManifest(
            id="match.constant",
            kind="scoring_model",
            version="1.0.0",
            api_version="1",
            capabilities=frozenset({"evaluate"}),
        ),
        ConstantScoringModel(),
    )
    registry.add(
        PluginManifest(
            id="extract.toy",
            kind="extraction",
            version="1.0.0",
            api_version="1",
            capabilities=frozenset({"extract"}),
        ),
        ToyExtractionStrategy(),
    )