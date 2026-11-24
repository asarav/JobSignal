"""keywords.* handlers (M9)."""

from __future__ import annotations

from typing import Any

from app.application.dto import (
    KeywordsBackfillParams,
    KeywordsCoverageParams,
    KeywordsExtractParams,
    KeywordsPrevalenceParams,
    KeywordsTrendsParams,
)
from app.application.keywords import KeywordInsightsService, KeywordPersistenceService
from app.domain.services.keywords import KeywordExtractor


class KeywordsHandlers:
    def __init__(self, container: Any = None) -> None:
        self._container = container
        self._extractor = KeywordExtractor()

    async def extract(self, params: KeywordsExtractParams) -> dict:
        return {"keywords": self._extractor.extract(params.text)}

    async def prevalence(self, params: KeywordsPrevalenceParams) -> dict:
        return await KeywordInsightsService(self._container).prevalence(
            company_id=params.company_id or None,
            min_sample=params.min_sample,
            limit=params.limit,
        )

    async def coverage(self, params: KeywordsCoverageParams) -> dict:
        return await KeywordInsightsService(self._container).coverage(
            company_id=params.company_id or None,
            profile_id=params.profile_id,
            min_sample=params.min_sample,
            limit=params.limit,
        )

    async def trends(self, params: KeywordsTrendsParams) -> dict:
        return await KeywordInsightsService(self._container).trends(
            company_id=params.company_id or None,
            bucket=params.bucket,
            periods=params.periods,
            min_sample=params.min_sample,
            limit=params.limit,
            keyword_ids=params.keyword_ids or None,
        )

    async def backfill(self, params: KeywordsBackfillParams) -> dict:
        return await KeywordPersistenceService(self._container).backfill(
            limit=params.limit
        )
