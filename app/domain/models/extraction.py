from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.domain.models.job import RawJob


@dataclass(frozen=True, slots=True)
class ExtractionTarget:
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
