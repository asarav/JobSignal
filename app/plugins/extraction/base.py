"""Shared plumbing for extraction strategies (M2).

A strategy only has to say *which fields it can see*; the base class turns
that into an :class:`ExtractionResult` with a raw job, the fields it found and
a confidence. Every strategy therefore reports the same shape, which is what
makes field-level merging possible.
"""

from __future__ import annotations

from typing import Any

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.extraction import ExtractionResult, ExtractionTarget
from app.domain.models.job import RawJob, RawJobRef


class StrategyBase:
    """Base class for extraction strategies."""

    id: str = ""
    version: str = "1.0.0"
    default_order: int = 100
    #: Smoke-test strategies are registered but never run in the pipeline.
    excluded_from_pipeline: bool = False

    def can_attempt(self, target: ExtractionTarget) -> bool:
        """Whether this strategy can look at the given target at all."""
        return True

    async def extract(
        self, target: ExtractionTarget, ctx: ExecutionContext
    ) -> Result[ExtractionResult, PortError]:
        fields = self.fields(target)
        if not fields:
            return Err(
                PortError(
                    code="extraction.no_fields",
                    message=f"{self.id} found no fields in {target.url}",
                    retryable=False,
                )
            )
        return Ok(self.to_result(target, fields))

    def fields(self, target: ExtractionTarget) -> dict[str, Any]:
        """The fields this strategy can see. Overridden by subclasses."""
        raise NotImplementedError

    def confidence(self, field_name: str) -> float:
        """Confidence for one field. Overridden by subclasses."""
        return 0.5

    def to_result(
        self, target: ExtractionTarget, fields: dict[str, Any]
    ) -> ExtractionResult:
        present = tuple(name for name, value in fields.items() if value not in (None, "", [], {}))
        per_field = {name: self.confidence(name) for name in present}
        total = sum(per_field.values())
        confidence = round(total / len(present), 2) if present else 0.0
        ref = RawJobRef(source_id=target.source_id, url=target.url)
        raw = RawJob(
            ref=ref,
            title=_as_text(fields.get("title")),
            body_text=_as_text(fields.get("description_text")),
            body_html=fields.get("description_html"),
            payload=dict(fields),
        )
        return ExtractionResult(
            raw=raw,
            fields_found=present,
            confidence=confidence,
            strategy_id=self.id,
            plugin_version=self.version,
            field_confidence=per_field,
        )


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    return str(value)
