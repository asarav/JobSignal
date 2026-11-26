"""ATS directory discovery provider (M4).

Yields career-site URLs from the bundled seed shards (``seeds/manifest.json``
plus one NDJSON file per connector; see :mod:`app.plugins.discovery.seed_shards`).

Two things matter about this provider:

* it is a **bootstrap**, not the discovery engine — it only gives a cold
  install something to crawl immediately;
* a pass reads a **bounded window** of the seed, ordered largest-first, and
  advances through it from the shared rotation cursor, instead of
  materialising all 35k candidates on every run.  ``bootstrap_candidates`` is
  the separate cold-start list the auto-discovery use case registers once.

Enumerating the seed needs no network; the crawler verifies each board and
records a failed crawl run for a dead one instead of aborting the pass.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.domain.models.common import Evidence
from app.domain.models.context import ExecutionContext
from app.domain.models.discovery import DiscoveredSource, DiscoveryQuery, ProviderCapabilities
from app.plugins.discovery.seed_shards import (
    MANIFEST_NAME,
    SeedCatalog,
    SeedRecord,
    read_manifest,
    records_from_legacy_rows,
)

logger = logging.getLogger(__name__)

#: Boards one pass hands out.  The registration cap is 200/pass, so a window of
#: a few hundred is enough to keep that cap fed without re-reading the seed.
DEFAULT_WINDOW = 200


class AtsDirectoryProvider:
    """Yields career-site URLs from the bundled seed shards."""

    id = "discovery.ats_directory"
    version = "2.0.0"
    capabilities = ProviderCapabilities(offline=True, requires_opt_in=False)

    def __init__(
        self,
        seeds: Sequence[dict[str, Any]] | None = None,
        *,
        catalog: SeedCatalog | None = None,
        window: int = DEFAULT_WINDOW,
    ) -> None:
        self._seeds: list[dict[str, Any]] = list(seeds or [])
        self._catalog = catalog
        self._window = max(1, window)

    # ------------------------------------------------------------- construction

    @classmethod
    def from_file(
        cls, path: Path, *, logger_override: logging.Logger | None = None
    ) -> AtsDirectoryProvider:
        """Load the legacy single-file seed (kept for old installs and tests).

        A parse failure is logged and yields an empty provider rather than
        silently pretending the seed is empty — a broken bootstrap should be
        visible in the log instead of looking like "no companies".
        """
        log = logger_override or logger
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            log.warning(
                "discovery.ats_directory.seed_unreadable",
                extra={"path": str(path), "error": str(exc)},
            )
            return cls(seeds=[])
        except ValueError as exc:
            log.warning(
                "discovery.ats_directory.seed_unparseable",
                extra={"path": str(path), "error": str(exc)},
            )
            return cls(seeds=[])
        if not isinstance(data, list):
            log.warning("discovery.ats_directory.seed_not_a_list", extra={"path": str(path)})
            return cls(seeds=[])
        return cls(seeds=[seed for seed in data if isinstance(seed, dict)])

    @classmethod
    def from_directory(
        cls,
        directory: Path,
        *,
        window: int = DEFAULT_WINDOW,
        logger_override: logging.Logger | None = None,
    ) -> AtsDirectoryProvider:
        """Load the sharded seed.  Falls back to the legacy file when absent."""
        log = logger_override or logger
        if not (directory / MANIFEST_NAME).exists():
            return cls.from_file(directory / "ats_tenants.json", logger_override=log)
        try:
            catalog = SeedCatalog.open(directory)
        except Exception as exc:  # noqa: BLE001 - a bad seed must not stop the worker
            log.warning(
                "discovery.ats_directory.manifest_invalid",
                extra={"path": str(directory), "error": str(exc)},
            )
            return cls(seeds=[])
        log.info(
            "discovery.ats_directory.loaded",
            extra={
                "path": str(directory),
                "boards": catalog.total,
                "hiring": catalog.hiring_total,
                "window": window,
            },
        )
        return cls(catalog=catalog, window=window)

    # ---------------------------------------------------------------- discovery

    @property
    def window(self) -> int:
        return self._window

    @property
    def catalog(self) -> SeedCatalog | None:
        return self._catalog

    def window_start(self, cursor: int, slices: int, total: int) -> int:
        """Where this pass starts in the ordered seed.

        ``cursor`` advances by ``slices`` every pass, so dividing it by the
        step gives a stable pass number; each pass takes the next window and
        wraps at the end.  Every board is proposed once per sweep instead of
        all of them on every pass.
        """
        if total <= 0:
            return 0
        pass_index = max(0, cursor) // max(1, slices)
        return (pass_index * self._window) % total

    async def discover(
        self, query: DiscoveryQuery, ctx: ExecutionContext
    ) -> AsyncIterator[DiscoveredSource]:
        if self._catalog is None:
            for source in self._legacy_sources():
                yield source
            return
        cursor = _as_int(query.extra.get("cursor"), 0)
        slices = _as_int(query.extra.get("slices"), 1)
        total = self._catalog.total
        if total <= 0:
            return
        start = self.window_start(cursor, slices, total)
        observed_at = datetime.now(UTC)
        for record in self._catalog.slice(start, self._window):
            yield _discovered(record, observed_at)

    def bootstrap_candidates(self, limit: int) -> list[DiscoveredSource]:
        """The largest hiring boards, for a cold start.

        Empty boards are deliberately absent: the bootstrap exists to make a
        fresh install immediately useful, and a board with no openings today
        is not useful yet.
        """
        if self._catalog is None or limit <= 0:
            return []
        observed_at = datetime.now(UTC)
        return [
            _discovered(record, observed_at) for record in self._catalog.bootstrap(limit)
        ]

    def _legacy_sources(self) -> list[DiscoveredSource]:
        observed_at = datetime.now(UTC)
        records = records_from_legacy_rows(self._seeds)
        return [_discovered(record, observed_at) for record in records]


def _discovered(record: SeedRecord, observed_at: datetime) -> DiscoveredSource:
    return DiscoveredSource(
        kind="ats",
        url=record.url,
        company_hint=record.company_hint,
        ats_hint=record.connector_id or None,
        confidence=0.9 if record.hiring else 0.6,
        evidence=[
            Evidence(
                kind="seed",
                description="bundled ATS seed shard",
                data={
                    "url": record.url,
                    "postings": record.postings,
                    "verified_at": record.verified_at,
                },
                observed_at=observed_at,
            )
        ],
        tenant_key=record.tenant_key,
        observed_postings=record.postings,
    )


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


#: Re-exported so a caller can load a catalog without importing the module twice.
__all__ = [
    "AtsDirectoryProvider",
    "DEFAULT_WINDOW",
    "SeedCatalog",
    "read_manifest",
]
