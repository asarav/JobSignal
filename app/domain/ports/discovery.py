from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from app.domain.models.context import ExecutionContext
from app.domain.models.discovery import DiscoveredSource, DiscoveryQuery, ProviderCapabilities


class DiscoveryProvider(Protocol):
    id: str
    version: str
    capabilities: ProviderCapabilities

    def discover(
        self, query: DiscoveryQuery, ctx: ExecutionContext
    ) -> AsyncIterator[DiscoveredSource]: ...
