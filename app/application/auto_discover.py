"""Automatic discovery and the daily crawl trigger (M4).

One run does four things:

1. asks every *enabled* discovery provider for candidate career sites,
   advancing the providers' rotation cursor by ``discovery.cursor_step``
   slices so repeated runs explore new ground instead of the same handful
   of seeds (a failed provider holds the cursor so its slice range is
   retried next pass),
2. registers up to ``discovery.max_new_sources_per_run`` unknown career sites
   as ``SourceConfig`` rows,
3. crawls the sources that are actually *due* (never crawled, or last attempt
   older than their own interval), stalest first, up to
   ``discovery.max_crawl_sources_per_run`` and in parallel up to the shared
   crawl gate, so a pass stays bounded even after hundreds of sources have
   been discovered,
4. records the completion time and the cursor in ``app_settings`` so both the
   daily cadence and the covered ground survive a worker restart.

Failures are isolated per provider and per source: one broken provider or a
dead board never aborts the pass.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from app.application.container import Container
from app.application.dto import AutoDiscoverResult
from app.application.sources import DEFAULT_CRAWL_INTERVAL_S, SourceRegistry, run_crawl
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.discovery import DiscoveredSource, DiscoveryQuery
from app.domain.models.source import SourceConfig
from app.domain.ports.http import HttpClient

#: Settings key holding the run state (``last_run_at``, ``rotation_cursor``).
AUTO_DISCOVER_STATE_KEY = "auto_discover_state"

#: How often the worker checks whether a daily run is due (seconds).
AUTO_DISCOVER_POLL_S = 300.0

#: Cadence of the automatic discovery + crawl pass (seconds).
AUTO_DISCOVER_INTERVAL_S = 24 * 3600.0

#: Grace period after worker start before the first pass (seconds). Keeps app
#: launch snappy and off the network while the UI is still coming up.
AUTO_DISCOVER_STARTUP_DELAY_S = 60.0

#: Caps that keep one pass bounded as the discovered source list grows.
DEFAULT_MAX_NEW_SOURCES_PER_RUN = 200
DEFAULT_MAX_CRAWL_SOURCES_PER_RUN = 60

#: How many rotation slices every pass walks (coverage per day).
DEFAULT_CURSOR_STEP = 12

#: Passes run back-to-back on a cold start. One pass registers at most
#: ``max_new_sources_per_run`` sources, so a fresh install needs several to
#: register the bundled seed list in minutes instead of over the first days.
DEFAULT_WARMUP_PASSES = 5

#: Shim timeout for a discovery provider call.  A pass walks a dozen index
#: slices under one gateway that throttles bursts, so the whole provider
#: call needs a generous ceiling (the pass is still bounded by its budgets).
DEFAULT_PROVIDER_TIMEOUT_S = 300.0


class _NoGate:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


_NO_GATE = _NoGate()


class AutoDiscoverUseCase:
    def __init__(
        self,
        container: Container,
        registry: SourceRegistry,
        http: HttpClient,
        *,
        crawl_gate: asyncio.Semaphore | None = None,
    ) -> None:
        self._container = container
        self._registry = registry
        self._http = http
        self._crawl_gate = crawl_gate

    async def run(self) -> AutoDiscoverResult:
        """Discover new career sites, register them, then crawl what is due."""
        state = await self._state()
        cursor = _as_int(state.get("rotation_cursor"), 0)
        discovered, providers_ok = await self._discover(cursor)
        registered = await self._register(discovered)
        crawled, failed = await self._crawl_due()

        # A provider that failed to answer holds the cursor, so the same
        # slice range is retried next pass instead of being skipped forever.
        state["rotation_cursor"] = cursor + self._cursor_step() if providers_ok else cursor
        state["last_run_at"] = self._container.clock.now().isoformat()
        await self._save_state(state)

        result = AutoDiscoverResult(
            discovered=len(discovered),
            registered=registered,
            crawled=crawled,
            failed=failed,
        )
        self._container.logger.info(
            "discovery.auto_discover.completed",
            discovered=result.discovered,
            registered=result.registered,
            crawled=result.crawled,
            failed=result.failed,
            rotation_cursor=cursor,
        )
        return result

    async def run_if_due(
        self, interval_s: float = AUTO_DISCOVER_INTERVAL_S
    ) -> AutoDiscoverResult | None:
        """Run only when the last completed run is older than ``interval_s``.

        On a cold start (no state at all) several passes run back-to-back so
        a fresh install registers the bundled seed list immediately instead
        of over the first ``warmup_passes`` days.
        """
        now = _as_utc(self._container.clock.now())
        last = await self._last_run_at()
        if last is None:
            if self._warmup_passes() <= 1:
                return await self.run()
            return await self.run_warmup()
        if now - last < timedelta(seconds=interval_s):
            return None
        return await self.run()

    async def run_warmup(self) -> AutoDiscoverResult:
        """Run back-to-back passes; for cold starts only.

        Each pass still obeys its own budgets and the shared crawl gate; the
        loop stops early once a pass registers nothing new (the seed list is
        exhausted and only slow index discovery remains, which the daily
        cadence covers).
        """
        passes = self._warmup_passes()
        total = AutoDiscoverResult()
        for index in range(passes):
            try:
                result = await self.run()
            except Exception as exc:  # noqa: BLE001 - warmup must not kill the worker
                self._container.logger.warning(
                    "discovery.auto_discover.warmup_failed",
                    index=index,
                    error=str(exc),
                )
                break
            total = AutoDiscoverResult(
                discovered=total.discovered + result.discovered,
                registered=total.registered + result.registered,
                crawled=total.crawled + result.crawled,
                failed=total.failed + result.failed,
            )
            self._container.logger.info(
                "discovery.auto_discover.warmup_pass",
                index=index + 1,
                of=passes,
                registered=result.registered,
                crawled=result.crawled,
            )
            if result.registered == 0:
                break
        return total

    # ------------------------------------------------------------------ discovery

    async def _discover(self, cursor: int) -> tuple[list[DiscoveredSource], bool]:
        """Collect candidates from every enabled provider, deduped by URL.

        Returns the candidates and whether every provider answered; a failed
        provider must not advance the rotation cursor (see ``run``).
        """
        plugin_registry = self._container.registry
        discovered: list[DiscoveredSource] = []
        seen: set[str] = set()
        all_answered = True
        for entry in plugin_registry.list_by_kind("discovery"):
            plugin_id = str(entry.manifest.id)
            if not plugin_registry.is_enabled(plugin_id):
                continue
            try:
                candidates = await plugin_registry.invoke(
                    plugin_id,
                    self._collect_provider,
                    entry.instance,
                    cursor,
                    self._cursor_step(),
                    timeout_s=self._provider_timeout_s(),
                )
            except Exception as exc:  # noqa: BLE001 - one provider must not break the run
                all_answered = False
                self._container.logger.warning(
                    "discovery.provider_failed", plugin_id=plugin_id, error=str(exc)
                )
                continue
            for source in candidates:
                key = _source_key(source.url)
                if not key or key in seen:
                    continue
                seen.add(key)
                discovered.append(source)
        return discovered, all_answered

    async def _collect_provider(
        self, provider: Any, cursor: int, slices: int
    ) -> list[DiscoveredSource]:
        """Materialize a provider's stream so the isolation shim covers it."""
        items: list[DiscoveredSource] = []
        query = DiscoveryQuery(extra={"cursor": cursor, "slices": slices})
        ctx = self._make_ctx()
        async for source in provider.discover(query, ctx):
            items.append(source)
        return items

    async def _register(self, discovered: Sequence[DiscoveredSource]) -> int:
        """Store unknown career sites as sources; existing URLs are skipped."""
        max_new = self._setting_int(
            "max_new_sources_per_run", DEFAULT_MAX_NEW_SOURCES_PER_RUN
        )
        known = {_source_key(s.url) for s in await self._registry.list_sources()}
        registered = 0
        for source in discovered:
            if registered >= max_new:
                self._container.logger.info(
                    "discovery.register_capped",
                    cap=max_new,
                    candidates=len(discovered),
                )
                break
            key = _source_key(source.url)
            if key in known:
                continue
            connector_id = source.ats_hint or self._registry.classify(source.url)
            try:
                await self._registry.add(
                    source.url,
                    connector_id=connector_id,
                    interval_s=self._default_interval_s(),
                )
            except Exception as exc:  # noqa: BLE001 - a bad URL must not abort the run
                self._container.logger.warning(
                    "discovery.register_failed", url=source.url, error=str(exc)
                )
                continue
            known.add(key)
            registered += 1
        return registered

    def _default_interval_s(self) -> float:
        """Per-source cadence for a new source: the configured default, jittered."""
        schedule = self._container.config.crawl.default_schedule
        base = float(schedule.interval_hours) * 3600.0
        jitter = float(schedule.jitter_pct) / 100.0
        return base * random.uniform(1.0 - jitter, 1.0 + jitter)

    # --------------------------------------------------------------------- crawl

    async def _crawl_due(self) -> tuple[int, int]:
        """Crawl the sources that are due; returns ``(crawled, failed)``.

        Crawls run concurrently up to the shared crawl gate, so a pass of
        sixty boards takes minutes rather than an hour of serial requests.
        """
        budget = self._setting_int(
            "max_crawl_sources_per_run", DEFAULT_MAX_CRAWL_SOURCES_PER_RUN
        )
        sources = await self._due_sources(budget)
        outcomes = await asyncio.gather(
            *(self._crawl_one(source) for source in sources)
        )
        crawled = sum(1 for ok in outcomes if ok)
        return crawled, len(outcomes) - crawled

    async def _crawl_one(self, source: SourceConfig) -> bool:
        """Crawl one source through the shared gate; ``True`` on success."""
        connector = self._registry.connector_for(source.connector_id)
        if connector is None:
            self._container.logger.warning(
                "discovery.no_connector",
                source_id=source.id,
                connector_id=source.connector_id,
            )
            return False
        try:
            async with self._crawl_gate or _NO_GATE:
                stats = await run_crawl(
                    source=source,
                    connector=connector,
                    uow_factory=self._container.uow_factory,
                    http=self._http,
                    clock_now=self._container.clock.now,
                    events=self._container.events,
                    pipeline=self._container.extraction_pipeline,
                    invoker=self._registry.invoker,
                )
        except Exception as exc:  # noqa: BLE001 - isolate the failing source
            self._container.logger.warning(
                "crawl.failed", source_id=source.id, error=str(exc)
            )
            return False
        if stats.get("error"):
            return False
        return True

    async def _due_sources(self, limit: int) -> list[SourceConfig]:
        """Enabled sources whose own interval has elapsed, stalest first."""
        now = _as_utc(self._container.clock.now())
        async with self._container.uow_factory() as uow:
            last_started = await uow.crawl_runs.last_started_by_source()
        pending: list[tuple[datetime, SourceConfig]] = []
        for source in await self._registry.list_sources(enabled_only=True):
            started = _parse_ts(last_started.get(source.id))
            interval = _source_interval_s(source)
            if started is not None and (now - started).total_seconds() < interval:
                continue
            pending.append((started or _EPOCH, source))
        pending.sort(key=lambda item: item[0])
        return [source for _started, source in pending[:limit]]

    # --------------------------------------------------------------------- state

    async def _state(self) -> dict[str, object]:
        async with self._container.uow_factory() as uow:
            stored = await uow.settings.get(AUTO_DISCOVER_STATE_KEY) or {}
        return dict(stored)

    async def _last_run_at(self) -> datetime | None:
        return _parse_ts((await self._state()).get("last_run_at"))

    async def _save_state(self, state: dict[str, object]) -> None:
        async with self._container.uow_factory() as uow:
            await uow.settings.put(AUTO_DISCOVER_STATE_KEY, dict(state))
            await uow.commit()

    def _setting_int(self, key: str, default: int) -> int:
        settings = getattr(self._container.config, "discovery", None)
        if isinstance(settings, dict):
            value = settings.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                return value
        return default

    def _cursor_step(self) -> int:
        return self._setting_int("cursor_step", DEFAULT_CURSOR_STEP)

    def _warmup_passes(self) -> int:
        """Warm-up passes; ``0`` disables the cold-start loop (unlike budgets)."""
        settings = getattr(self._container.config, "discovery", None)
        if isinstance(settings, dict):
            value = settings.get("warmup_passes")
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
        return DEFAULT_WARMUP_PASSES

    def _provider_timeout_s(self) -> float:
        """Shim timeout for one discovery provider call.

        Index queries legitimately take tens of seconds, so discovery gets a
        longer leash than the default plugin timeout.
        """
        settings = getattr(self._container.config, "discovery", None)
        if isinstance(settings, dict):
            value = settings.get("provider_timeout_s")
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value > 0
            ):
                return float(value)
        return DEFAULT_PROVIDER_TIMEOUT_S

    def _make_ctx(self) -> ExecutionContext:
        return ExecutionContext(
            task_id="auto_discover",
            clock=self._container.clock,
            http=self._http,
            logger=self._container.logger,
            cancel=CancellationToken(),
            deadline=None,
            config={},
        )


_EPOCH = datetime.min.replace(tzinfo=UTC)


def _source_key(url: str) -> str:
    """Dedupe key for a career-site URL: host + path, no scheme/trailing slash."""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    path = parts.path.rstrip("/")
    return f"{host}{path}"


def _source_interval_s(source: SourceConfig) -> float:
    """The crawl interval a source declares, in seconds."""
    raw = source.schedule.get("interval_s")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return DEFAULT_CRAWL_INTERVAL_S
    return max(0.0, float(raw))


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return _as_utc(datetime.fromisoformat(raw.replace("Z", "+00:00")))
    except ValueError:
        return None
