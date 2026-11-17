"""Field-level merge of extraction results (M2).

Several strategies may find the same field. The merge keeps, per field, the
value with the highest confidence; on a tie the earlier strategy in the
pipeline wins, because the pipeline is ordered by trust (structured data
first, heuristics last).
"""

from __future__ import annotations

from collections.abc import Sequence

from app.domain.models.extraction import (
    ExtractionResult,
    FieldValue,
    MergedExtraction,
)
from app.domain.models.job import RawJob, RawJobRef


def merge_results(results: Sequence[ExtractionResult]) -> MergedExtraction:
    """Merge extraction results field by field."""
    winners: dict[str, FieldValue] = {}
    used: list[str] = []
    raw: RawJob | None = None

    for result in results:
        if raw is None:
            raw = result.raw
        if result.strategy_id not in used:
            used.append(result.strategy_id)
        for name in result.fields_found:
            value = result.raw.payload.get(name)
            if value in (None, "", [], {}):
                continue
            confidence = result.field_confidence.get(name, result.confidence)
            current = winners.get(name)
            if current is None or confidence > current.confidence:
                winners[name] = FieldValue(
                    value=value, confidence=confidence, strategy_id=result.strategy_id
                )

    merged_raw = _build_raw(raw, winners)
    return MergedExtraction(raw=merged_raw, fields=winners, strategies_used=tuple(used))


def _build_raw(raw: RawJob | None, winners: dict[str, FieldValue]) -> RawJob:
    """Build the raw job that carries the merged field values."""
    if raw is None:
        return RawJob(
            ref=RawJobRef(source_id="", url=""),
            title=None,
            body_text=None,
            body_html=None,
            payload={},
        )
    title = winners.get("title")
    description = winners.get("description_text")
    html = winners.get("description_html")
    return RawJob(
        ref=raw.ref,
        title=str(title.value) if title else raw.title,
        body_text=str(description.value) if description else raw.body_text,
        body_html=html.value if html else raw.body_html,
        payload={name: entry.value for name, entry in winners.items()},
    )
