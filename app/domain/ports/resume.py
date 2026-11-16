from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from app.domain.models.common import PortError, Result
from app.domain.models.resume import (
    KeywordContext,
    KeywordMention,
    NormalizedKeyword,
    ParsedResume,
    ResumeFileRef,
)


class ResumeParser(Protocol):
    id: str
    supported_mime_types: frozenset[str]

    async def parse(self, file: ResumeFileRef) -> Result[ParsedResume, PortError]: ...


class KeywordExtractor(Protocol):
    id: str

    def extract(self, text: str, *, context: KeywordContext) -> Sequence[KeywordMention]: ...


class KeywordNormalizer(Protocol):
    taxonomy_version: str

    def normalize(self, mention: KeywordMention) -> NormalizedKeyword | None: ...
