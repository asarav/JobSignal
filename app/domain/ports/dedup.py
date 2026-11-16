from __future__ import annotations

from typing import Protocol

from app.domain.models.identity import IdentityDecision, JobFingerprintSet, SimilarityEvidence
from app.domain.models.job import NormalizedJob


class DeduplicationStrategy(Protocol):
    id: str
    version: str

    def compare(self, a: JobFingerprintSet, b: JobFingerprintSet) -> SimilarityEvidence: ...


class JobIdentityResolver(Protocol):
    async def resolve(self, incoming: NormalizedJob) -> IdentityDecision: ...
