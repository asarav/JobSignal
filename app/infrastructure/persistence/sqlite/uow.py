from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiosqlite

from app.domain.ports.repositories import (
    AliasRepository,
    ApplicationRepository,
    CompanyRepository,
    CrawlEventRepository,
    CrawlRunRepository,
    JobRepository,
    JobUserStateRepository,
    KeywordRepository,
    ObservationRepository,
    SearchProfileRepository,
    SnapshotRepository,
    SourceRepository,
    SuppressionRepository,
)
from app.infrastructure.persistence.sqlite.applications import (
    SqliteApplicationRepository,
    SqliteJobUserStateRepository,
    SqliteSuppressionRepository,
)
from app.infrastructure.persistence.sqlite.backups import (
    SqliteBackupRunRepository,
)
from app.infrastructure.persistence.sqlite.company_identity import (
    SqliteCompanyMergeRepository,
    SqliteJobKeywordRepository,
)
from app.infrastructure.persistence.sqlite.intelligence import (
    SqliteIntelligenceRepositories,
)
from app.infrastructure.persistence.sqlite.migrator import apply_pragmas, migrate
from app.infrastructure.persistence.sqlite.repositories import (
    SqliteAliasRepository,
    SqliteCompanyRepository,
    SqliteCrawlEventRepository,
    SqliteCrawlRunRepository,
    SqliteDedupRepository,
    SqliteJobRepository,
    SqliteObservationRepository,
    SqlitePluginStateRepository,
    SqliteSearchProfileRepository,
    SqliteSettingsRepository,
    SqliteSnapshotRepository,
    SqliteSourceRepository,
    SqliteTaskRepository,
)
from app.infrastructure.persistence.sqlite.resume_match import (
    SqliteMatchRepository,
    SqliteResumeRepository,
)
from app.infrastructure.persistence.sqlite.weights import (
    SqliteFitWeightRepository,
)


class _Unimplemented:
    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"repository method '{name}' is available in a later milestone")


class SqliteUnitOfWork:
    def __init__(self, db_path: Path, clock_now: Callable[[], Any]) -> None:
        self._db_path = db_path
        self._clock_now = clock_now
        self._conn: aiosqlite.Connection | None = None
        self.search_profiles: SearchProfileRepository = _Unimplemented()  # type: ignore[assignment]
        self.snapshots: SnapshotRepository = _Unimplemented()  # type: ignore[assignment]
        self.aliases: AliasRepository = _Unimplemented()  # type: ignore[assignment]
        self.dedup = _Unimplemented()
        self.sources: SourceRepository = _Unimplemented()  # type: ignore[assignment]
        self.crawl_runs: CrawlRunRepository = _Unimplemented()  # type: ignore[assignment]
        self.crawl_events: CrawlEventRepository = _Unimplemented()  # type: ignore[assignment]
        self.jobs: JobRepository = _Unimplemented()  # type: ignore[assignment]
        self.companies: CompanyRepository = _Unimplemented()  # type: ignore[assignment]
        self.applications: ApplicationRepository = _Unimplemented()  # type: ignore[assignment]
        self.suppressions: SuppressionRepository = _Unimplemented()  # type: ignore[assignment]
        self.job_user_state: JobUserStateRepository = _Unimplemented()  # type: ignore[assignment]
        self.observations: ObservationRepository = _Unimplemented()  # type: ignore[assignment]
        self.resumes = SqliteResumeRepository(self._conn, self._clock_now)
        self.matches = SqliteMatchRepository(self._conn, self._clock_now)
        self.intelligence: Any = _Unimplemented()
        self.company_merges: Any = _Unimplemented()
        self.weight_versions: Any = _Unimplemented()
        self.backups: Any = _Unimplemented()
        self.keywords: KeywordRepository = _Unimplemented()  # type: ignore[assignment]
        self.settings = None  # type: ignore[assignment]
        self.tasks = None  # type: ignore[assignment]
        self.plugins = None  # type: ignore[assignment]

    async def __aenter__(self) -> SqliteUnitOfWork:
        self._conn = await aiosqlite.connect(self._db_path)
        await apply_pragmas(self._conn)
        self._conn.row_factory = aiosqlite.Row
        self.settings = SqliteSettingsRepository(self._conn, self._clock_now)
        self.tasks = SqliteTaskRepository(self._conn)
        self.plugins = SqlitePluginStateRepository(self._conn, self._clock_now)
        self.sources = SqliteSourceRepository(self._conn, self._clock_now)
        self.companies = SqliteCompanyRepository(self._conn, self._clock_now)
        self.jobs = SqliteJobRepository(self._conn, self._clock_now)
        self.observations = SqliteObservationRepository(self._conn, self._clock_now)
        self.crawl_runs = SqliteCrawlRunRepository(self._conn, self._clock_now)
        self.crawl_events = SqliteCrawlEventRepository(self._conn, self._clock_now)
        self.snapshots = SqliteSnapshotRepository(self._conn, self._clock_now)
        self.aliases = SqliteAliasRepository(self._conn, self._clock_now)
        self.dedup = SqliteDedupRepository(self._conn, self._clock_now)
        self.search_profiles = SqliteSearchProfileRepository(self._conn, self._clock_now)
        self.applications = SqliteApplicationRepository(self._conn, self._clock_now)
        self.suppressions = SqliteSuppressionRepository(self._conn, self._clock_now)
        self.job_user_state = SqliteJobUserStateRepository(self._conn, self._clock_now)
        self.resumes = SqliteResumeRepository(self._conn, self._clock_now)
        self.matches = SqliteMatchRepository(self._conn, self._clock_now)
        self.intelligence = SqliteIntelligenceRepositories(self._conn)
        self.company_merges = SqliteCompanyMergeRepository(self._conn)
        self.keywords = SqliteJobKeywordRepository(self._conn)
        self.weight_versions = SqliteFitWeightRepository(self._conn)
        self.backups = SqliteBackupRunRepository(self._conn)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._conn is not None:
            if any(exc):
                await self._conn.rollback()
            await self._conn.close()
            self._conn = None

    async def commit(self) -> None:
        if self._conn is None:
            raise RuntimeError("unit of work is closed")
        await self._conn.commit()

    async def rollback(self) -> None:
        if self._conn is not None:
            await self._conn.rollback()


class SqliteUnitOfWorkFactory:
    def __init__(self, db_path: Path, clock_now: Callable[[], Any]) -> None:
        self._db_path = db_path
        self._clock_now = clock_now

    @property
    def db_path(self) -> Path:
        return self._db_path

    async def initialize(self) -> int:
        """Apply pending migrations and return the resulting schema version."""
        return await migrate(self._db_path)

    def __call__(self) -> SqliteUnitOfWork:
        return SqliteUnitOfWork(self._db_path, self._clock_now)
