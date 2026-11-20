"""ATS directory discovery provider (M4).

Yields career-site URLs from a bundled seed list of known public ATS
tenants.  Enumerating the seeds needs no network; the registered sources are
verified by the crawler, which records a failed crawl run for dead boards
instead of aborting the pass.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.domain.models.common import Evidence
from app.domain.models.context import ExecutionContext
from app.domain.models.discovery import DiscoveredSource, DiscoveryQuery, ProviderCapabilities


class AtsDirectoryProvider:
    """Yields career-site URLs from a bundled seed list."""

    id = "discovery.ats_directory"
    version = "1.0.0"
    capabilities = ProviderCapabilities(offline=True, requires_opt_in=False)

    def __init__(self, seeds: Sequence[dict[str, Any]] | None = None) -> None:
        self._seeds: list[dict[str, Any]] = list(seeds or [])

    @classmethod
    def from_file(cls, path: Path) -> AtsDirectoryProvider:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = []
        if not isinstance(data, list):
            data = []
        return cls(seeds=[seed for seed in data if isinstance(seed, dict)])

    async def discover(
        self, query: DiscoveryQuery, ctx: ExecutionContext
    ) -> AsyncIterator[DiscoveredSource]:
        observed_at = datetime.now(UTC)
        for seed in self._seeds:
            url = seed.get("url")
            if not isinstance(url, str) or not url:
                continue
            company = seed.get("company_hint")
            ats = seed.get("ats_hint")
            tenant_key = seed.get("tenant_key")
            confidence = seed.get("confidence", 0.9)
            yield DiscoveredSource(
                kind=str(seed.get("kind", "ats")),
                url=url,
                company_hint=company if isinstance(company, str) else None,
                ats_hint=ats if isinstance(ats, str) else None,
                confidence=float(confidence) if isinstance(confidence, (int, float)) else 0.9,
                evidence=[
                    Evidence(
                        kind="seed",
                        description="bundled ATS directory seed",
                        data={"url": url},
                        observed_at=observed_at,
                    )
                ],
                tenant_key=tenant_key if isinstance(tenant_key, str) and tenant_key else None,
            )
