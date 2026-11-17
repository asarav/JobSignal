"""Extraction models: targets, results, and field-level merges (M2)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.domain.models.enums import ExtractionStatus
from app.domain.models.job import RawJob


@dataclass(frozen=True, slots=True)
class ExtractionTarget:
    """The material an extraction strategy may look at."""

    url: str
    source_id: str
    html: str | None = None
    json_payload: Mapping[str, object] | None = None
    content_type: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    raw: RawJob
    fields_found: tuple[str, ...]
    confidence: float
    strategy_id: str
    plugin_version: str
    content_hash: str | None = None
    extra: Mapping[str, object] = field(default_factory=dict)
    #: Confidence per field, so a merge can compare fields, not whole results.
    field_confidence: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class FieldValue:
    """One extracted field, with the confidence of the strategy that found it."""

    value: Any
    confidence: float
    strategy_id: str

    def __bool__(self) -> bool:
        return self.value is not None and self.value != ""


@dataclass(frozen=True, slots=True)
class MergedExtraction:
    """The result of running several strategies over one target.

    ``fields`` holds the winning value per field (highest confidence wins;
    ties keep the earlier strategy). ``strategies_used`` records the pipeline
    path, which the UI shows as provenance.
    """

    raw: RawJob
    fields: Mapping[str, FieldValue] = field(default_factory=dict)
    strategies_used: tuple[str, ...] = ()

    def value(self, name: str) -> Any:
        entry = self.fields.get(name)
        return entry.value if entry else None

    def confidence(self, name: str) -> float:
        entry = self.fields.get(name)
        return entry.confidence if entry else 0.0

    def provenance(self) -> Mapping[str, str]:
        """Field -> the strategy that produced the winning value."""
        return {name: entry.strategy_id for name, entry in self.fields.items()}

    @property
    def completeness(self) -> float:
        """Share of the expected job fields that were filled."""
        expected = ("title", "description_text", "locations", "employment_type")
        if not expected:
            return 0.0
        filled = sum(1 for name in expected if self.fields.get(name))
        return round(filled / len(expected), 2)

    @property
    def status(self) -> ExtractionStatus:
        if self.completeness >= 0.75:
            return ExtractionStatus.COMPLETE
        if self.completeness > 0:
            return ExtractionStatus.PARTIAL
        return ExtractionStatus.FAILED
