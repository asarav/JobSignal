from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol

from app.domain.models.common import PortError, Result
from app.domain.models.context import ExecutionContext
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import ConnectorCapabilities, MatchResult, PageSniff, SourceConfig


class JobSourceConnector(Protocol):
    id: str
    version: str
    capabilities: ConnectorCapabilities

    def matches(self, url: str, sniff: PageSniff | None = None) -> MatchResult: ...

    async def list_jobs(
        self, source: SourceConfig, ctx: ExecutionContext
    ) -> AsyncIterator[RawJobRef]: ...

    async def fetch_job(
        self, ref: RawJobRef, source: SourceConfig, ctx: ExecutionContext
    ) -> Result[RawJob, PortError]:
        """Fetch one posting.

        The source is passed in because a connector often needs the board's
        tenant (or company) to build the request URL.
        """
        ...
