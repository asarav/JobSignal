"""Extraction pipeline (M2).

The pipeline runs the registered strategies in order and merges their results
field by field. A source may override the order (``SourceConfig.extraction_order``)
when it knows which strategy works best for it.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.models.common import Ok, PortError
from app.domain.models.context import ExecutionContext
from app.domain.models.extraction import ExtractionResult, ExtractionTarget, MergedExtraction
from app.domain.ports.extraction import ExtractionStrategy
from app.domain.services.merge import merge_results


class ExtractionPipeline:
    """Runs extraction strategies in order and merges what they find."""

    def __init__(self, strategies: Sequence[ExtractionStrategy]) -> None:
        self._strategies = list(strategies)

    @property
    def strategies(self) -> Sequence[ExtractionStrategy]:
        return tuple(self._strategies)

    def ordered(self, override: Sequence[str] = ()) -> list[ExtractionStrategy]:
        """Strategies in pipeline order, with ``override`` ids moved to the front."""
        by_id = {strategy.id: strategy for strategy in self._strategies}
        ordered: list[ExtractionStrategy] = []
        for strategy_id in override:
            strategy = by_id.pop(strategy_id, None)
            if strategy is not None:
                ordered.append(strategy)
        rest = sorted(
            by_id.values(), key=lambda s: (getattr(s, "default_order", 100), s.id)
        )
        ordered.extend(rest)
        return ordered

    async def extract(
        self,
        target: ExtractionTarget,
        ctx: ExecutionContext,
        *,
        order: Sequence[str] = (),
    ) -> MergedExtraction:
        """Run the pipeline over ``target`` and return the merged result.

        A strategy that fails is skipped: one broken strategy must not stop
        the others from filling the record.
        """
        results: list[ExtractionResult] = []
        for strategy in self.ordered(order):
            if not strategy.can_attempt(target):
                continue
            try:
                outcome = await strategy.extract(target, ctx)
            except PortError:
                continue
            if isinstance(outcome, Ok):
                results.append(outcome.value)
        return merge_results(results)
