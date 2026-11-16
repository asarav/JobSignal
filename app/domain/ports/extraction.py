from __future__ import annotations

from typing import Protocol

from app.domain.models.common import PortError, Provenance, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.extraction import ExtractionResult, ExtractionTarget
from app.domain.models.job import NormalizedJob, RawJob


class ExtractionStrategy(Protocol):
    id: str
    version: str
    default_order: int

    def can_attempt(self, target: ExtractionTarget) -> bool: ...

    async def extract(
        self, target: ExtractionTarget, ctx: ExecutionContext
    ) -> Result[ExtractionResult, PortError]: ...


class JobNormalizer(Protocol):
    def normalize(self, raw: RawJob, provenance: Provenance) -> NormalizedJob: ...
