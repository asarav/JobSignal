"""keywords.* handlers."""

from __future__ import annotations

from app.application.dto import KeywordsExtractParams
from app.domain.services.keywords import KeywordExtractor


class KeywordsHandlers:
    def __init__(self) -> None:
        self._extractor = KeywordExtractor()

    async def extract(self, params: KeywordsExtractParams) -> dict:
        return {"keywords": self._extractor.extract(params.text)}
