"""Automatic discovery and the continuous crawl trigger (M4/M6).

Discovery is not a once-a-day event: the worker runs bounded passes back to
back with a short pause, so coverage keeps growing and every board already in
the registry is re-checked as its tier interval comes due.  One pass does four
things:

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
   pass cadence and the covered ground survive a worker restart.

Failures are isolated per provider and per source: one broken provider or a
dead board never aborts the pass.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from app.application.board_probe import BoardProbe, ProbeTarget
from app.application.config import discovery_settings
from app.application.container import Container
from app.application.dto import AutoDiscoverResult
from app.application.sources import (
    DEFAULT_CRAWL_INTERVAL_S,
    FALLBACK_CONNECTOR_ID,
    SourceRegistry,
    run_crawl,
    verify_posting,
)
from app.application.tiers import TierService
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.discovery import (
    DiscoveredPosting,
    DiscoveredSource,
    DiscoveryQuery,
)
from app.domain.models.source import SourceConfig
from app.domain.ports.http import HttpClient
from app.domain.services.board_identity import identity_key
from app.domain.services.tiers import (
    DEFAULT_TIER_INTERVALS_S,
    TIERS,
    interval_for_tier,
)

#: Settings key holding the run state (``last_run_at``, ``rotation_cursor``).
AUTO_DISCOVER_STATE_KEY = "auto_discover_state"

#: How often the worker checks whether a pass is due (seconds).
AUTO_DISCOVER_POLL_S = 60.0

#: Default pause between automatic discovery + crawl passes (seconds).  Each
#: pass stays bounded by the existing source/crawl/funnel caps and the global
#: crawl gate; the pause is what keeps a continuous loop polite to the public
#: indexes.  Override with ``discovery.auto_discover_interval_minutes``.
AUTO_DISCOVER_INTERVAL_S = 3600.0

#: Grace period after worker start before the first pass (seconds).  Zero:
#: discovery and crawling begin as soon as the worker is ready, not after the
#: first minute.  The loop still yields to the IPC server between passes.
AUTO_DISCOVER_STARTUP_DELAY_S = 0.0

#: Pause between catch-up passes while any enabled board has never been
#: crawled.  Much shorter than the normal hourly cadence so a new install works
#: through its seed instead of waiting an interval per sixty boards.
AUTO_DISCOVER_CATCH_UP_PAUSE_S = 10.0

#: Sources whose tier is re-decided in one pass.  The registry can hold the
#: whole seed; rotating a window keeps a pass from touching tens of thousands of
#: rows while still covering every board over a handful of passes.
DEFAULT_TIER_SOURCES_PER_PASS = 2_000

#: Boards one catch-up pass may start.  The shared crawl gate still limits how
#: many requests are in flight, and the configured per-pass cap always applies
#: when it is larger; this only shortens the first sweep of a fresh seed.
DEFAULT_CATCH_UP_CRAWL_SOURCES_PER_RUN = 240

#: Boards selected per continuous backlog batch.  Deliberately larger than the
#: crawl gate so the gate always has queued work instead of idling between
#: batches; the gate and per-origin rate limits decide the real request rate.
DEFAULT_BACKLOG_BATCH = 400

#: Settings key for the background full-seed import marker.
SEED_IMPORT_STATE_KEY = "seed_import_state"

#: Caps that keep one pass bounded as the discovered source list grows.
DEFAULT_MAX_NEW_SOURCES_PER_RUN = 200
DEFAULT_MAX_CRAWL_SOURCES_PER_RUN = 60

#: How many rotation slices every pass walks.
DEFAULT_CURSOR_STEP = 12

#: Posting URLs the funnel may verify in one pass (one fetch each).  This is a
#: new budget, never a raise of the existing crawl/source caps.
DEFAULT_FUNNEL_POSTINGS_PER_PASS = 50

#: Index slices one UTC day may walk across every pass.  A pass spends
#: ``cursor_step`` slices, so this covers the shipped hourly cadence; it only
#: limits how much a much shorter pause can multiply the load on the public
#: indexes.  ``0`` pauses index discovery entirely (crawling continues).
DEFAULT_MAX_INDEX_SLICES_PER_DAY = 288

#: Passes run back-to-back on a cold start. One pass registers at most
#: ``max_new_sources_per_run`` sources, so a fresh install needs several to
#: register the bundled seed list in minutes instead of over the first days.
DEFAULT_WARMUP_PASSES = 5

#: Shim timeout for a discovery provider call.  A pass walks a dozen index
#: slices under one gateway that throttles bursts, so the whole provider
#: call needs a generous ceiling (the pass is still bounded by its budgets).
DEFAULT_PROVIDER_TIMEOUT_S = 300.0

#: Candidates probed before registering in one pass.  Each probe is one HTTP
#: request, so the budget is what keeps a fifty-percent-dead index from turning
#: registration into a crawl of the archive's mistakes.
DEFAULT_PROBE_CANDIDATES_PER_PASS = 50

#: Hiring seed boards registered per pass in steady state (the cold-start
#: bootstrap is separate and much larger).  Reserving this share keeps the seed
#: tail moving even when live discovery fills the normal cap.
DEFAULT_SEED_BOARDS_PER_PASS = 25

#: Boards the first-run bootstrap registers, largest hiring first.
DEFAULT_BOOTSTRAP_SEED_SOURCES = 2_000

#: Live-but-empty boards promoted out of probation per pass.  They are kept
#: (a quiet employer hires later) but never take a normal registration slot.
DEFAULT_EMPTY_BOARDS_PER_PASS = 5

#: Live-but-empty sources crawled per pass.  6k empties at a weekly cadence is
#: ~860 crawls/day, so the tier is capped independently of the crawl budget.
DEFAULT_EMPTY_CRAWL_SOURCES_PER_PASS = 5

#: Candidates a pass may leave unregistered without losing them.
DEFAULT_CANDIDATE_BACKLOG_LIMIT = 500

#: Cadence of a live-but-empty or repeatedly failing source.
SLOW_INTERVAL_S = 604_800.0

#: How long a probe answer is trusted, by state.  A dead board is re-asked
#: after three months because a retired tenant can reopen.
PROBE_TTL_S: dict[str, float] = {
    "hiring": 86_400.0,
    "live_empty": 2_592_000.0,
    "dead": 7_776_000.0,
    "unknown": 3_600.0,
}

#: Settings keys inside the auto-discovery state blob.
PROBE_CACHE_KEY = "probe_cache"
PROBATION_KEY = "probation"
BACKLOG_KEY = "register_backlog"
DISMISSED_KEY = "dismissed"
YIELD_KEY = "yield"


@dataclass(frozen=True, slots=True)
class RegistrationStats:
    """What one pass's registration stage did (surfaced in the result)."""

    registered: int = 0
    probed: int = 0
    live: int = 0
    dead: int = 0
    empty: int = 0
    queued: int = 0
    by_connector: dict[str, int] = field(default_factory=dict)


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
        self._board_probe = BoardProbe(http)
        #: Serializes source-crawl selection between a pass and the startup
        #: backlog drain so the same board is not started twice.
        self._crawl_lock = asyncio.Lock()
        #: Config can be refreshed by the worker after a settings update; the
        #: container itself is immutable and fixed at startup.
        self._config: Any = container.config
        self._pass_cache: dict[object, object] = {}
        #: Cached answer for the never-crawled check; the seed can hold tens of
        #: thousands of rows and the worker asks every few seconds while it
        #: catches up, so a full scan runs at most once a minute.
        self._pending_checked_at: float | None = None
        self._pending_value = 0
        #: Per-connector slice weights for this pass (user interest + yield).
        self._pass_weights: dict[str, float] = {}
        # Continuous passes can overlap with a manual ``discovery.auto_discover``
        # request; serialize them so one pass never double-spends its budgets.
        self._run_lock = asyncio.Lock()

    def refresh_config(self, config: Any) -> None:
        """Adopt freshly stored settings without rebuilding the worker."""
        self._config = config

    async def run(self, *, catch_up: bool = False) -> AutoDiscoverResult:
        """Run one bounded pass; concurrent callers wait for the current one."""
        async with self._run_lock:
            return await self._run_once(catch_up=catch_up)

    async def _run_once(self, *, catch_up: bool = False) -> AutoDiscoverResult:
        """Discover career sites, harvest/verify postings, then crawl what is due."""
        started_at = self._container.clock.now().isoformat()
        # The UI subscribes to this so a background pass is visible while it
        # works, not only after it finishes.
        self._container.events.publish(
            "discovery.pass_started", {"started_at": started_at}
        )
        state = await self._state()
        cursor = _as_int(state.get("rotation_cursor"), 0)
        # Board discovery and the funnel walk the same index slices; one cache
        # per pass keeps the funnel from paying for the same queries twice.
        self._pass_cache = {}
        today = _as_utc(self._container.clock.now()).date().isoformat()
        slices = (
            _as_int(state.get("index_slices"), 0)
            if state.get("index_day") == today
            else 0
        )
        slice_budget = self._setting_non_negative(
            "max_index_slices_per_day", DEFAULT_MAX_INDEX_SLICES_PER_DAY
        )
        discovery_ran = False
        if slices < slice_budget:
            discovered, postings, providers_ok = await self._discover(cursor)
            discovery_ran = True
            if self._discovery_provider_count():
                slices += self._cursor_step()
        else:
            # The day's index budget is spent: keep crawling what is due, but
            # do not query the public indexes again until tomorrow.
            discovered, postings, providers_ok = [], [], True
            self._container.logger.info(
                "discovery.index_budget_exhausted",
                budget=slice_budget,
                spent=slices,
            )
        registration = await self._register(state, discovered)
        registered = registration.registered
        postings_verified = 0
        tiers_changed = 0
        if self._funnel_enabled():
            postings_verified = await self._verify_postings(postings)
            try:
                # With the full seed registered, tiering every board every pass
                # would dominate the pass.  A rotating window keeps each pass
                # bounded and still re-decides every board within a few passes.
                enabled = await self._registry.list_sources(enabled_only=True)
                start = _as_int(state.get("tier_cursor"), 0)
                tier_sources = _rotating_window(
                    enabled, start, DEFAULT_TIER_SOURCES_PER_PASS
                )
                if enabled:
                    state["tier_cursor"] = (
                        start + len(tier_sources)
                    ) % len(enabled)
                tiers_changed = len(
                    await TierService(self._container).assign(tier_sources)
                )
            except Exception as exc:  # noqa: BLE001 - tiering must not abort a pass
                self._container.logger.warning(
                    "discovery.tiering_failed", error=str(exc)
                )
        crawled, failed = await self._crawl_due(catch_up=catch_up)

        # A provider that failed to answer holds the cursor, so the same
        # slice range is retried next pass instead of being skipped forever.
        # A pass that never queried the index (budget spent) must not advance
        # it either: no slices were covered.
        state["rotation_cursor"] = (
            cursor + self._cursor_step()
            if discovery_ran and providers_ok
            else cursor
        )
        state["index_day"] = today
        state["index_slices"] = slices
        state["last_run_at"] = self._container.clock.now().isoformat()

        result = AutoDiscoverResult(
            discovered=len(discovered),
            registered=registered,
            crawled=crawled,
            failed=failed,
            postings_harvested=len(postings),
            postings_verified=postings_verified,
            tiers_changed=tiers_changed,
            probed=registration.probed,
            live=registration.live,
            dead=registration.dead,
            empty=registration.empty,
            queued=registration.queued,
        )
        state["last_summary"] = result.model_dump()
        await self._save_state(state)
        self._container.events.publish(
            "discovery.pass_completed",
            {
                "started_at": started_at,
                "finished_at": self._container.clock.now().isoformat(),
                **result.model_dump(),
            },
        )
        self._container.logger.info(
            "discovery.auto_discover.completed",
            discovered=result.discovered,
            registered=result.registered,
            crawled=result.crawled,
            failed=result.failed,
            postings_harvested=result.postings_harvested,
            postings_verified=result.postings_verified,
            tiers_changed=result.tiers_changed,
            probed=result.probed,
            live=result.live,
            dead=result.dead,
            empty=result.empty,
            queued=result.queued,
            rotation_cursor=cursor,
        )
        return result

    async def status(self) -> dict[str, Any]:
        """Report the background cadence without running or spending a pass."""
        state = await self._state()
        interval_s = self._interval_seconds()
        last = _parse_ts(state.get("last_run_at"))
        now = _as_utc(self._container.clock.now())
        next_at = (
            last + timedelta(seconds=interval_s)
            if last is not None
            else now + timedelta(seconds=AUTO_DISCOVER_STARTUP_DELAY_S)
        )
        sources = await self._registry.list_sources()
        async with self._container.uow_factory() as uow:
            runs = await uow.crawl_runs.list(limit=1)
            await uow.commit()
        last_crawl: str | None = None
        if runs:
            row = runs[0]
            raw = row.get("finished_at") or row.get("started_at")
            last_crawl = raw if isinstance(raw, str) else None
        raw_summary = state.get("last_summary")
        # While boards are waiting for their first crawl the worker passes
        # again within seconds, not after a full interval.
        if await self.pending_initial_crawls() > 0 and last is not None:
            next_at = last + timedelta(seconds=AUTO_DISCOVER_CATCH_UP_PAUSE_S)
        return {
            "running": self._run_lock.locked(),
            "interval_minutes": max(1, round(interval_s / 60)),
            "last_pass_at": last.isoformat() if last is not None else None,
            "next_pass_at": next_at.isoformat(),
            "last_summary": dict(raw_summary)
            if isinstance(raw_summary, dict)
            else {},
            "sources": len(sources),
            "enabled_sources": sum(1 for source in sources if source.enabled),
            "last_crawl_at": last_crawl,
            "note": (
                "A pass is bounded and repeats in the background, so more "
                "postings keep arriving over time."
            ),
        }

    def _interval_seconds(self) -> float:
        """Pause between automatic passes, from the same settings the loop uses."""
        settings = self._settings()
        minutes = settings.get("auto_discover_interval_minutes")
        if (
            isinstance(minutes, (int, float))
            and not isinstance(minutes, bool)
            and minutes > 0
        ):
            return float(minutes) * 60.0
        hours = settings.get("auto_discover_interval_hours")
        if (
            isinstance(hours, (int, float))
            and not isinstance(hours, bool)
            and hours > 0
        ):
            return float(hours) * 3600.0
        return AUTO_DISCOVER_INTERVAL_S

    async def run_if_due(
        self,
        interval_s: float = AUTO_DISCOVER_INTERVAL_S,
        *,
        catch_up: bool = False,
    ) -> AutoDiscoverResult | None:
        """Run when the last completed run is older than ``interval_s``.

        ``catch_up`` skips the interval: the worker sets it while boards are
        still waiting for their first crawl, so a launch works through the
        backlog instead of crawling sixty boards and then idling for an hour.
        Its own budgets and the shared crawl gate still bound every pass.

        On a cold start (no state at all) several passes run back-to-back so
        a fresh install registers the bundled seed list immediately instead
        of over the first ``warmup_passes`` passes.
        """
        now = _as_utc(self._container.clock.now())
        last = await self._last_run_at()
        if last is None:
            if self._warmup_passes() <= 1:
                return await self.run()
            return await self.run_warmup()
        if not catch_up and now - last < timedelta(seconds=interval_s):
            return None
        return await self.run(catch_up=catch_up)

    async def run_warmup(self) -> AutoDiscoverResult:
        """Run back-to-back passes; for cold starts only.

        Each pass still obeys its own budgets and the shared crawl gate; the
        loop stops early once a pass registers nothing new (the seed list is
        exhausted and only slow index discovery remains, which the continuous
        cadence covers).
        """
        passes = self._warmup_passes()
        total = AutoDiscoverResult()
        # Serialized with the startup seed import: both decide what is new from
        # an identity snapshot, so running them together could add a board twice.
        async with self._run_lock:
            bootstrapped = await self._bootstrap_seed()
        if bootstrapped:
            total.registered += bootstrapped
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
                postings_harvested=(
                    total.postings_harvested + result.postings_harvested
                ),
                postings_verified=(
                    total.postings_verified + result.postings_verified
                ),
                tiers_changed=total.tiers_changed + result.tiers_changed,
                probed=total.probed + result.probed,
                live=total.live + result.live,
                dead=total.dead + result.dead,
                empty=total.empty + result.empty,
                queued=result.queued,
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

    async def _discover(
        self, cursor: int
    ) -> tuple[list[DiscoveredSource], list[DiscoveredPosting], bool]:
        """Collect board and posting candidates from every enabled provider.

        Returns the candidates and whether every provider answered; a failed
        provider must not advance the rotation cursor (see ``run``).  A
        provider that can harvest posting URLs is asked for those instead of
        boards (the bundled ``discovery.funnel`` provider is exactly that).
        """
        self._pass_weights = await self._slice_weights()
        plugin_registry = self._container.registry
        discovered: list[DiscoveredSource] = []
        postings: list[DiscoveredPosting] = []
        seen: set[str] = set()
        seen_postings: set[str] = set()
        all_answered = True
        for entry in plugin_registry.list_by_kind("discovery"):
            plugin_id = str(entry.manifest.id)
            if not plugin_registry.is_enabled(plugin_id):
                continue
            harvesting = hasattr(entry.instance, "discover_postings")
            if harvesting and not self._funnel_enabled():
                continue
            try:
                if harvesting:
                    harvested = await plugin_registry.invoke(
                        plugin_id,
                        self._collect_postings,
                        entry.instance,
                        cursor,
                        self._cursor_step(),
                        timeout_s=self._provider_timeout_s(),
                    )
                    for posting in harvested:
                        if posting.url and posting.url not in seen_postings:
                            seen_postings.add(posting.url)
                            postings.append(posting)
                    continue
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
        return discovered, postings, all_answered

    async def _collect_provider(
        self, provider: Any, cursor: int, slices: int
    ) -> list[DiscoveredSource]:
        """Materialize a provider's stream so the isolation shim covers it."""
        items: list[DiscoveredSource] = []
        query = DiscoveryQuery(
            extra={
                "cursor": cursor,
                "slices": slices,
                "target_weights": dict(self._pass_weights),
            }
        )
        ctx = self._make_ctx()
        async for source in provider.discover(query, ctx):
            items.append(source)
        return items

    async def _slice_weights(self) -> dict[str, float]:
        """How much index effort each connector deserves next pass.

        Two user-side signals, both cheap: sources the user added by hand (the
        clearest vote for an ATS) and how many boards that connector's last
        slices produced.  Every connector keeps a weight of at least 1, so a
        quiet target is still explored.  Providers that support weighted
        allocation (``discovery.domain_index``) read ``target_weights``.
        """
        weights: dict[str, float] = {}
        try:
            async with self._container.uow_factory() as uow:
                interest = await uow.settings.get("discovery_interest") or {}
        except Exception:  # noqa: BLE001 - a signal must never fail discovery
            interest = {}
        if isinstance(interest, dict):
            for key, value in interest.items():
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    weights[str(key)] = 1.0 + min(3.0, value / 5.0)
        produced = (await self._state()).get(YIELD_KEY)
        if isinstance(produced, dict):
            for key, value in produced.items():
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    weights[str(key)] = weights.get(str(key), 1.0) + min(
                        2.0, value / 10.0
                    )
        return weights

    async def _collect_postings(
        self, provider: Any, cursor: int, slices: int
    ) -> list[DiscoveredPosting]:
        """Materialize a provider's posting-URL stream for the shim."""
        items: list[DiscoveredPosting] = []
        query = DiscoveryQuery(extra={"cursor": cursor, "slices": slices})
        ctx = self._make_ctx()
        async for posting in provider.discover_postings(query, ctx):
            items.append(posting)
        return items

    async def _register(
        self, state: dict[str, object], discovered: Sequence[DiscoveredSource]
    ) -> RegistrationStats:
        """Probe, order and store candidates; never lose the ones over the cap.

        * Board identity (not the raw URL) decides what is already known, and
          archived sources count: a board the user removed must not come back.
        * A live candidate is probed first (bounded per pass).  A dead board is
          dropped, a hiring board registers, and a confidently empty board goes
          to probation instead of taking a registration slot.
        * Seed boards already carry their posting count, so an empty seed board
          skips the probe entirely and cannot displace a hiring one.
        * Candidates over the budget are persisted to a backlog and drained by
          the next pass instead of being discarded.
        """
        max_new = self._setting_int(
            "max_new_sources_per_run", DEFAULT_MAX_NEW_SOURCES_PER_RUN
        )
        seed_budget = self._setting_int(
            "seed_boards_per_pass", DEFAULT_SEED_BOARDS_PER_PASS
        )
        max_probes = self._setting_int(
            "probe_candidates_per_pass", DEFAULT_PROBE_CANDIDATES_PER_PASS
        )
        empty_budget = self._setting_int(
            "empty_boards_per_pass", DEFAULT_EMPTY_BOARDS_PER_PASS
        )
        backlog_limit = self._setting_int(
            "candidate_backlog_limit", DEFAULT_CANDIDATE_BACKLOG_LIMIT
        )
        now = _as_utc(self._container.clock.now())

        taken = await self._taken_identities()
        known_urls = {
            _source_key(s.url) for s in await self._registry.list_sources()
        }
        raw_dismissed = state.get(DISMISSED_KEY)
        dismissed = (
            {item for item in raw_dismissed if isinstance(item, str)}
            if isinstance(raw_dismissed, list)
            else set()
        )
        raw_cache = state.get(PROBE_CACHE_KEY)
        cache: dict[str, object] = (
            dict(raw_cache) if isinstance(raw_cache, dict) else {}
        )
        raw_probation = state.get(PROBATION_KEY)
        probation = (
            [row for row in raw_probation if isinstance(row, dict)]
            if isinstance(raw_probation, list)
            else []
        )
        raw_backlog = state.get(BACKLOG_KEY)
        backlog = (
            [row for row in raw_backlog if isinstance(row, dict)]
            if isinstance(raw_backlog, list)
            else []
        )

        queued = [_candidate_from_row(row) for row in backlog]
        candidates = [item for item in queued if item is not None] + list(discovered)
        seed_candidates = [item for item in candidates if _is_seed_candidate(item)]
        live_candidates = [item for item in candidates if not _is_seed_candidate(item)]

        deferred: list[DiscoveredSource] = []
        new_probation: list[dict[str, object]] = []
        registered = 0
        probed = 0
        live = 0
        dead = 0
        empty = 0
        by_connector: dict[str, int] = {}

        def note(source: DiscoveredSource) -> None:
            connector = source.ats_hint or ""
            by_connector[connector] = by_connector.get(connector, 0) + 1

        def claim(source: DiscoveredSource, ident: str) -> None:
            taken.add(ident)
            known_urls.add(_source_key(source.url))

        # Reserve a share of the cap for the seed tail, so live discovery
        # cannot starve it forever.  With no seed candidates the whole cap goes
        # to live discovery.
        seed_reserved = (
            min(seed_budget, max(1, max_new // 4)) if seed_candidates else 0
        )
        live_cap = max(0, max_new - seed_reserved)

        # Promote a few empty boards from an earlier pass first.  They keep the
        # slow cadence and never compete with a hiring board for a normal slot.
        # Draining before the candidate walk also means re-observing a board
        # that is still in probation cannot block its own promotion.
        drained = 0
        remaining_probation: list[dict[str, object]] = []
        probation_idents: set[str] = set()
        for row in probation:
            if drained >= empty_budget:
                remaining_probation.append(row)
                continue
            source = _candidate_from_row(row)
            ident = (
                identity_key(source.ats_hint, source.url, source.tenant_key)
                if source is not None
                else ""
            )
            if source is None or not ident or ident in taken or ident in dismissed:
                continue
            try:
                await self._add_source(
                    source, interval_s=SLOW_INTERVAL_S, probe_status="live_empty", postings=0
                )
            except Exception as exc:  # noqa: BLE001 - one bad row must not stop the pass
                self._container.logger.warning(
                    "discovery.register_failed", url=source.url, error=str(exc)
                )
                continue
            claim(source, ident)
            registered += 1
            empty += 1
            drained += 1
            note(source)
        for row in remaining_probation:
            pending = _candidate_from_row(row)
            if pending is None:
                continue
            pending_ident = identity_key(
                pending.ats_hint, pending.url, pending.tenant_key
            )
            if pending_ident:
                probation_idents.add(pending_ident)

        for source in live_candidates:
            ident = identity_key(source.ats_hint, source.url, source.tenant_key)
            if not ident or ident in taken or ident in dismissed:
                continue
            if _source_key(source.url) in known_urls:
                continue
            entry = _fresh_probe(cache, ident, now)
            if entry is None:
                if probed >= max_probes:
                    deferred.append(source)
                    continue
                outcome = await self._board_probe.probe(
                    ProbeTarget(source.ats_hint, source.url, source.tenant_key)
                )
                probed += 1
                cache[ident] = {
                    "state": outcome.state,
                    "postings": outcome.postings,
                    "at": now.isoformat(),
                }
                state_name: str = outcome.state
                postings = outcome.postings
            else:
                raw_state = entry.get("state")
                state_name = raw_state if isinstance(raw_state, str) else "unknown"
                raw_postings = entry.get("postings")
                postings = raw_postings if isinstance(raw_postings, int) else None
            if state_name == "dead":
                dead += 1
                claim(source, ident)
                continue
            if state_name == "live_empty":
                if ident not in probation_idents:
                    new_probation.append(_candidate_row(source, "live_empty", 0))
                    probation_idents.add(ident)
                claim(source, ident)
                continue
            if registered >= live_cap:
                deferred.append(source)
                continue
            await self._add_source(
                source,
                interval_s=self._default_interval_s(),
                probe_status="hiring" if state_name == "hiring" else None,
                postings=postings,
            )
            claim(source, ident)
            registered += 1
            live += 1
            note(source)

        for source in seed_candidates:
            ident = identity_key(source.ats_hint, source.url, source.tenant_key)
            if not ident or ident in taken or ident in dismissed:
                continue
            if _source_key(source.url) in known_urls:
                continue
            observed = source.observed_postings
            if observed == 0:
                if ident not in probation_idents:
                    new_probation.append(_candidate_row(source, "live_empty", 0))
                    probation_idents.add(ident)
                claim(source, ident)
                continue
            if registered >= max_new:
                deferred.append(source)
                continue
            await self._add_source(
                source,
                interval_s=self._default_interval_s(),
                probe_status="hiring" if observed else None,
                postings=observed,
            )
            claim(source, ident)
            registered += 1
            live += 1
            note(source)

        state[PROBE_CACHE_KEY] = _prune_probe_cache(cache, now)
        state[PROBATION_KEY] = (new_probation + remaining_probation)[:1000]
        state[BACKLOG_KEY] = [
            _candidate_row(item) for item in deferred[:backlog_limit]
        ]
        state[YIELD_KEY] = by_connector

        stats = RegistrationStats(
            registered=registered,
            probed=probed,
            live=live,
            dead=dead,
            empty=empty,
            queued=len(deferred[:backlog_limit]),
            by_connector=by_connector,
        )
        if deferred:
            self._container.logger.info(
                "discovery.register_backlog",
                queued=stats.queued,
                candidates=len(candidates),
                cap=max_new,
            )
        self._container.logger.info(
            "discovery.register_completed",
            registered=stats.registered,
            probed=stats.probed,
            live=stats.live,
            dead=stats.dead,
            empty=stats.empty,
            queued=stats.queued,
        )
        return stats

    async def _add_source(
        self,
        source: DiscoveredSource,
        *,
        interval_s: float,
        probe_status: str | None = None,
        postings: int | None = None,
    ) -> None:
        """Register one board and record what the probe observed about it."""
        connector_id = source.ats_hint or self._registry.classify(source.url)
        stored = await self._registry.add(
            source.url,
            connector_id=connector_id,
            interval_s=interval_s,
            tenant_key=source.tenant_key,
            record_interest=False,
        )
        if probe_status is None and postings is None:
            return
        schedule = dict(stored.schedule)
        if probe_status is not None:
            schedule["probe_status"] = probe_status
        if postings is not None:
            schedule["observed_postings"] = postings
        async with self._container.uow_factory() as uow:
            row = await uow.sources.get(stored.id)
            if row is not None:
                await uow.sources.save(replace(row, schedule=schedule))
                await uow.commit()

    async def _taken_identities(self) -> set[str]:
        """Boards already known, including the ones the user removed."""
        taken: set[str] = set()
        for source in list(await self._registry.list_sources()) + list(
            await self._registry.list_archived()
        ):
            ident = identity_key(source.connector_id, source.url, source.ats_tenant_key)
            if ident:
                taken.add(ident)
        return taken

    async def import_seed(self) -> int:
        """Register every board in the bundled seed, before the first crawl.

        It shares the pass lock, so a bootstrap or a live-discovery pass cannot
        register the same board from a stale identity snapshot at the same
        time.  A completed sweep is nearly free on later launches.
        """
        async with self._run_lock:
            return await self._import_seed_locked()

    async def _import_seed_locked(self) -> int:
        """Register every board in the bundled seed, in the background.

        The cold-start bootstrap fills the first minutes with the largest
        hiring boards; this sweep continues with the rest of the catalogue so
        a launch reaches the whole seed instead of the slow ``seed_boards_per
        pass`` tail.  It is idempotent and resumable: known and archived boards
        are skipped, and a marker recording the manifest total skips a sweep
        that already finished for the same seed.  Registration is offline; the
        crawl itself stays bounded by the normal per-pass budgets.
        """
        provider: Any = None
        for entry in self._container.registry.list_by_kind("discovery"):
            if not self._container.registry.is_enabled(str(entry.manifest.id)):
                continue
            if callable(getattr(entry.instance, "seed_candidates", None)):
                provider = entry.instance
                break
        if provider is None:
            return 0
        catalog = getattr(provider, "catalog", None)
        total = int(getattr(catalog, "total", 0) or 0)
        marker = await self._seed_import_state()
        if total > 0 and marker.get("total") == total and marker.get("completed_at"):
            return 0
        taken = await self._taken_identities()
        registered = 0
        seen = 0
        failed_batches = 0
        batch: list[SourceConfig] = []

        async def flush() -> None:
            """Insert one batch in a single transaction."""
            nonlocal registered, failed_batches
            if not batch:
                return
            try:
                async with self._container.uow_factory() as uow:
                    for item in batch:
                        await uow.sources.add(item)
                    await uow.commit()
            except Exception as exc:  # noqa: BLE001 - retry this batch next launch
                failed_batches += 1
                self._container.logger.warning(
                    "discovery.seed_import_batch_failed",
                    size=len(batch),
                    error=str(exc),
                )
            else:
                registered += len(batch)
                self._container.logger.info(
                    "discovery.seed_import.progress",
                    registered=registered,
                    seen=seen,
                    total=total,
                )
            batch.clear()
            # Let the IPC server, the crawl pass and the UI breathe.
            await asyncio.sleep(0)

        for source in provider.seed_candidates():
            seen += 1
            ident = identity_key(source.ats_hint, source.url, source.tenant_key)
            if ident is None or ident in taken:
                continue
            connector_id = source.ats_hint or self._registry.classify(source.url)
            connector_id = connector_id or FALLBACK_CONNECTOR_ID
            postings = source.observed_postings
            status = None if postings is None else (
                "hiring" if postings > 0 else "live_empty"
            )
            schedule: dict[str, object] = {"interval_s": self._default_interval_s()}
            if status is not None:
                schedule["probe_status"] = status
            if postings is not None:
                schedule["observed_postings"] = postings
            batch.append(
                SourceConfig(
                    id=f"src_{uuid.uuid4().hex[:12]}",
                    url=source.url,
                    kind=(
                        "ats"
                        if connector_id != FALLBACK_CONNECTOR_ID
                        else "career_site"
                    ),
                    connector_id=connector_id,
                    ats_tenant_key=source.tenant_key,
                    schedule=schedule,
                )
            )
            taken.add(ident)
            if len(batch) >= 500:
                await flush()
        await flush()
        if failed_batches == 0:
            await self._save_seed_import_state(
                {
                    "total": total,
                    "registered": registered,
                    "completed_at": self._container.clock.now().isoformat(),
                }
            )
        self._container.logger.info(
            "discovery.seed_import.completed",
            registered=registered,
            failed_batches=failed_batches,
            total=total,
        )
        return registered

    async def pending_initial_crawls(self, *, max_age_s: float = 60.0) -> int:
        """Enabled boards that have never had a crawl attempt.

        While this is non-zero the worker runs passes back-to-back instead of
        waiting for the normal interval, so a freshly imported seed starts
        producing postings immediately.  Dead boards and the probation queue
        of verified-empty jobs are excluded: they are crawled on the normal
        cadence, and counting them would never let catch-up finish.
        """
        now = asyncio.get_running_loop().time()
        if (
            self._pending_checked_at is not None
            and now - self._pending_checked_at < max_age_s
        ):
            return self._pending_value
        async with self._container.uow_factory() as uow:
            started = await uow.crawl_runs.last_started_by_source()
        pending = 0
        for source in await self._registry.list_sources(enabled_only=True):
            if source.id in started:
                continue
            if source.schedule.get("probe_status") in {"dead", "live_empty"}:
                continue
            if self._registry.connector_for(source.connector_id) is None:
                continue
            pending += 1
        self._pending_value = pending
        self._pending_checked_at = now
        return pending

    async def _seed_import_state(self) -> dict[str, object]:
        async with self._container.uow_factory() as uow:
            stored = await uow.settings.get(SEED_IMPORT_STATE_KEY) or {}
        return dict(stored)

    async def _save_seed_import_state(self, state: dict[str, object]) -> None:
        async with self._container.uow_factory() as uow:
            await uow.settings.put(SEED_IMPORT_STATE_KEY, dict(state))
            await uow.commit()

    async def _bootstrap_seed(self) -> int:
        """Register the largest hiring seed boards on a cold start.

        This is a separate budget from ``max_new_sources_per_run``: the seed is
        a warm start, so the first minutes should produce a useful corpus
        instead of 200 boards an hour for a week.  Empty boards do not
        participate; they enter probation later.
        """
        limit = self._setting_non_negative(
            "bootstrap_seed_sources", DEFAULT_BOOTSTRAP_SEED_SOURCES
        )
        if limit <= 0:
            return 0
        candidates: list[DiscoveredSource] = []
        for entry in self._container.registry.list_by_kind("discovery"):
            if not self._container.registry.is_enabled(str(entry.manifest.id)):
                continue
            provider = entry.instance
            bootstrap = getattr(provider, "bootstrap_candidates", None)
            if callable(bootstrap):
                try:
                    candidates = list(bootstrap(limit))
                except Exception as exc:  # noqa: BLE001 - a bad seed must not abort
                    self._container.logger.warning(
                        "discovery.bootstrap_failed", error=str(exc)
                    )
                break
        if not candidates:
            return 0
        taken = await self._taken_identities()
        registered = 0
        for source in candidates:
            ident = identity_key(source.ats_hint, source.url, source.tenant_key)
            if not ident or ident in taken:
                continue
            try:
                await self._add_source(
                    source,
                    interval_s=self._default_interval_s(),
                    probe_status="hiring",
                    postings=source.observed_postings,
                )
            except Exception as exc:  # noqa: BLE001 - one bad URL must not abort
                self._container.logger.warning(
                    "discovery.bootstrap_register_failed",
                    url=source.url,
                    error=str(exc),
                )
                continue
            taken.add(ident)
            registered += 1
            if registered >= limit:
                break
        self._container.logger.info(
            "discovery.bootstrap_completed",
            registered=registered,
            candidates=len(candidates),
        )
        return registered

    # -------------------------------------------------------------------- funnel

    async def _verify_postings(self, postings: Sequence[DiscoveredPosting]) -> int:
        """Verify harvested posting URLs, capped by the funnel budget.

        Each verification is one connector detail fetch through the shared
        crawl gate; it can never raise the crawl/source caps because it runs
        in its own bounded batch before the due-source crawl.  Only postings
        that resolve to a registered source are considered (the others cannot
        be fetched), and the budget is spread across boards instead of being
        consumed by the first tenant in the index answer.
        """
        if not postings:
            return 0
        budget = self._funnel_postings_per_pass()
        sources = await self._registry.list_sources(enabled_only=True)
        by_key = {_source_key(source.url): source for source in sources}
        by_tenant: dict[str, list[SourceConfig]] = {}
        for source in sources:
            tenant = _tenant_of_source(source)
            if tenant:
                by_tenant.setdefault(tenant, []).append(source)

        def resolve(posting: DiscoveredPosting) -> SourceConfig | None:
            """The registered source a harvested posting belongs to.

            Posting URLs and board URLs can sit on sibling hosts (Greenhouse
            serves ``boards.`` and ``job-boards.``), and board discovery
            registers only the host it saw, so the connector and tenant are
            the fallback identity.
            """
            exact = by_key.get(_source_key(posting.board_url))
            if exact is not None:
                return exact
            tenant = posting.tenant_key or _tenant_of_url(posting.board_url)
            candidates = by_tenant.get(tenant, []) if tenant else []
            if posting.connector_id:
                for candidate in candidates:
                    if candidate.connector_id == posting.connector_id:
                        return candidate
            return candidates[0] if candidates else None

        selected, unregistered = _select_postings(postings, resolve, budget)
        if unregistered:
            self._container.logger.info(
                "discovery.funnel.unregistered_postings",
                count=unregistered,
                candidates=len(postings),
            )
        if not selected:
            return 0

        async def verify(source: SourceConfig, posting: DiscoveredPosting) -> bool:
            connector = self._registry.connector_for(source.connector_id)
            if connector is None:
                return False
            try:
                async with self._crawl_gate or _NO_GATE:
                    outcome = await verify_posting(
                        source=source,
                        connector=connector,
                        url=posting.url,
                        source_posting_id=posting.source_posting_id,
                        uow_factory=self._container.uow_factory,
                        http=self._http,
                        clock_now=self._container.clock.now,
                        events=self._container.events,
                        pipeline=self._container.extraction_pipeline,
                        invoker=self._registry.invoker,
                        config=self._container.config,
                    )
            except Exception as exc:  # noqa: BLE001 - isolate one bad posting
                self._container.logger.warning(
                    "discovery.funnel.verify_failed", url=posting.url, error=str(exc)
                )
                return False
            return outcome != "failed"

        outcomes = await asyncio.gather(
            *(verify(source, posting) for source, posting in selected)
        )
        verified = sum(1 for ok in outcomes if ok)
        eligible = len(postings) - unregistered
        if eligible > budget:
            self._container.logger.info(
                "discovery.funnel.capped", cap=budget, candidates=eligible
            )
        return verified

    def _discovery_provider_count(self) -> int:
        """How many discovery providers a pass will actually ask.

        Mirrors ``_discover``: a disabled provider is skipped, and a harvesting
        provider is skipped when the funnel is off.
        """
        count = 0
        for entry in self._container.registry.list_by_kind("discovery"):
            if not self._container.registry.is_enabled(str(entry.manifest.id)):
                continue
            if hasattr(entry.instance, "discover_postings") and not self._funnel_enabled():
                continue
            count += 1
        return count

    def _funnel_enabled(self) -> bool:
        return funnel_enabled(self._settings())

    def _funnel_postings_per_pass(self) -> int:
        return self._setting_int(
            "funnel_postings_per_pass", DEFAULT_FUNNEL_POSTINGS_PER_PASS
        )

    def _default_interval_s(self) -> float:
        """Per-source cadence for a new source: the configured default, jittered."""
        schedule = self._container.config.crawl.default_schedule
        base = float(schedule.interval_hours) * 3600.0
        jitter = float(schedule.jitter_pct) / 100.0
        return base * random.uniform(1.0 - jitter, 1.0 + jitter)

    # --------------------------------------------------------------------- crawl

    async def crawl_backlog_batch(
        self, limit: int = DEFAULT_BACKLOG_BATCH
    ) -> tuple[int, int]:
        """Crawl a batch of boards that have never had a crawl attempt.

        The worker calls this back-to-back while a fresh seed is being worked
        through, so the shared crawl gate stays busy instead of being fed a
        small bite every few seconds.  Each batch is larger than the gate on
        purpose: the gate (and the per-origin rate limiter) still decide how
        many requests actually run, while there is always work queued.
        """
        async with self._crawl_lock:
            budget = max(1, int(limit))
            due = await self._due_sources(budget, never_started_only=True)
            selected = [
                source
                for source in due
                if source.schedule.get("probe_status") not in {"dead", "live_empty"}
                and self._registry.connector_for(source.connector_id) is not None
            ][:budget]
            if not selected:
                return 0, 0
            outcomes = await asyncio.gather(
                *(self._crawl_one(source) for source in selected)
            )
        crawled = sum(1 for ok in outcomes if ok)
        return crawled, len(outcomes) - crawled

    async def _crawl_due(self, *, catch_up: bool = False) -> tuple[int, int]:
        """Crawl the sources that are due; returns ``(crawled, failed)``.

        Crawls run concurrently up to the shared crawl gate, so a pass of
        sixty boards takes minutes rather than an hour of serial requests.
        A catch-up pass may start more boards, but never fewer than the
        configured budget, and the gate still bounds the requests in flight.
        """
        budget = self._setting_int(
            "max_crawl_sources_per_run", DEFAULT_MAX_CRAWL_SOURCES_PER_RUN
        )
        if catch_up:
            budget = max(budget, DEFAULT_CATCH_UP_CRAWL_SOURCES_PER_RUN)
        empty_cap = self._setting_int(
            "empty_crawl_sources_per_pass", DEFAULT_EMPTY_CRAWL_SOURCES_PER_PASS
        )
        async with self._crawl_lock:
            # Ask for more than the budget because dead and empty sources are
            # filtered out below; then take normal sources first and let only a
            # few empty ones through.
            due = await self._due_sources(budget + empty_cap + 20)
            normal: list[SourceConfig] = []
            empties: list[SourceConfig] = []
            for source in due:
                status = source.schedule.get("probe_status")
                if status == "dead":
                    continue
                if status == "live_empty":
                    empties.append(source)
                else:
                    normal.append(source)
            selected = (normal[:budget] + empties[:empty_cap])[: budget + empty_cap]
            outcomes = await asyncio.gather(
                *(self._crawl_one(source) for source in selected)
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
                    config=self._container.config,
                )
        except Exception as exc:  # noqa: BLE001 - isolate the failing source
            self._container.logger.warning(
                "crawl.failed", source_id=source.id, error=str(exc)
            )
            await self._note_crawl_outcome(source, ok=False)
            return False
        ok = not stats.get("error")
        await self._note_crawl_outcome(source, ok=ok)
        return ok

    async def _note_crawl_outcome(self, source: SourceConfig, *, ok: bool) -> None:
        """Back a repeatedly failing board off; keep an empty one slow.

        A source that fails two crawls in a row is far more likely dead than
        temporarily broken, so it drops to the weekly cadence and stops taking
        a hot/warm slot.  It is never deleted: the failure stays visible and the
        user can remove it.
        """
        schedule = dict(source.schedule)
        if ok:
            if "probe_failures" not in schedule:
                return
            schedule.pop("probe_failures", None)
        else:
            raw_failures = schedule.get("probe_failures")
            failures = (
                raw_failures if isinstance(raw_failures, int) else 0
            ) + 1
            schedule["probe_failures"] = failures
            if failures >= 2:
                schedule["probe_status"] = "dead"
                schedule["interval_s"] = SLOW_INTERVAL_S
        try:
            async with self._container.uow_factory() as uow:
                row = await uow.sources.get(source.id)
                if row is not None:
                    await uow.sources.save(replace(row, schedule=schedule))
                    await uow.commit()
        except Exception as exc:  # noqa: BLE001 - bookkeeping must not fail a pass
            self._container.logger.warning(
                "discovery.crawl_outcome_failed", source_id=source.id, error=str(exc)
            )

    async def _due_sources(
        self, limit: int, *, never_started_only: bool = False
    ) -> list[SourceConfig]:
        """Enabled sources whose own interval has elapsed, stalest first."""
        now = _as_utc(self._container.clock.now())
        async with self._container.uow_factory() as uow:
            last_started = await uow.crawl_runs.last_started_by_source()
        pending: list[tuple[datetime, SourceConfig]] = []
        for source in await self._registry.list_sources(enabled_only=True):
            started = _parse_ts(last_started.get(source.id))
            if never_started_only and started is not None:
                continue
            interval = self._effective_interval_s(source)
            if started is not None and (now - started).total_seconds() < interval:
                continue
            pending.append((started or _EPOCH, source))
        pending.sort(key=lambda item: item[0])
        return [source for _started, source in pending[:limit]]

    def _effective_interval_s(self, source: SourceConfig) -> float:
        """The source's crawl interval, narrowed by its relevance tier."""
        return effective_crawl_interval_s(source, self._settings())

    async def is_due(self, source_id: str, *, now: datetime | None = None) -> bool:
        """Whether a source's effective interval has elapsed.

        The per-source scheduler asks this before crawling so a source that an
        auto-discovery pass just refreshed is not fetched again seconds later.
        """
        source = await self._registry.get(source_id)
        if source is None:
            return False
        moment = _as_utc(now or self._container.clock.now())
        async with self._container.uow_factory() as uow:
            last_started = await uow.crawl_runs.last_started_by_source()
        started = _parse_ts(last_started.get(source.id))
        if started is None:
            return True
        interval = self._effective_interval_s(source)
        return (moment - started).total_seconds() >= interval

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

    def _settings(self) -> dict[str, Any]:
        return discovery_settings(self._config)

    def _setting_int(self, key: str, default: int) -> int:
        value = self._settings().get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
        return default

    def _setting_non_negative(self, key: str, default: int) -> int:
        """Like ``_setting_int`` but ``0`` is meaningful ("pause this")."""
        value = self._settings().get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return default

    def _cursor_step(self) -> int:
        return self._setting_int("cursor_step", DEFAULT_CURSOR_STEP)

    def _warmup_passes(self) -> int:
        """Warm-up passes; ``0`` disables the cold-start loop (unlike budgets)."""
        value = self._settings().get("warmup_passes")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        return DEFAULT_WARMUP_PASSES

    def _provider_timeout_s(self) -> float:
        """Shim timeout for one discovery provider call.

        Index queries legitimately take tens of seconds, so discovery gets a
        longer leash than the default plugin timeout.
        """
        value = self._settings().get("provider_timeout_s")
        if (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and value > 0
        ):
            return float(value)
        return DEFAULT_PROVIDER_TIMEOUT_S

    def _make_ctx(self) -> ExecutionContext:
        cache = getattr(self, "_pass_cache", None)
        return ExecutionContext(
            task_id="auto_discover",
            clock=self._container.clock,
            http=self._http,
            logger=self._container.logger,
            cancel=CancellationToken(),
            deadline=None,
            config={"discovery_cache": cache} if isinstance(cache, dict) else {},
        )


_EPOCH = datetime.min.replace(tzinfo=UTC)


def funnel_enabled(settings: object) -> bool:
    """Whether the relevance funnel (tiering + harvesting) is enabled."""
    value = _settings_mapping(settings).get("funnel_enabled")
    if isinstance(value, bool):
        return value
    return True


def effective_crawl_interval_s(source: SourceConfig, settings: object) -> float:
    """The interval a source must wait between crawls.

    Relevance tiers narrow the cadence only while the funnel is enabled; with
    the funnel off (or before a tier has been assigned) the source keeps its
    plain due-based interval.  Shared by the auto-discovery pass and the
    per-source scheduler so both honour the same rule.
    """
    if funnel_enabled(settings):
        tier = source.schedule.get("tier")
        if isinstance(tier, str) and tier in TIERS:
            raw = _settings_mapping(settings).get("tier_intervals_s")
            intervals = DEFAULT_TIER_INTERVALS_S
            if isinstance(raw, (list, tuple)) and len(raw) == 3:
                cast_values = [value for value in raw if not isinstance(value, bool)]
                if len(cast_values) == 3 and all(
                    isinstance(value, (int, float)) and value >= 0
                    for value in cast_values
                ):
                    intervals = (
                        float(cast_values[0]),
                        float(cast_values[1]),
                        float(cast_values[2]),
                    )
            return interval_for_tier(tier, intervals)
    return _source_interval_s(source)


def _settings_mapping(settings: object) -> Mapping[str, Any]:
    """Read a discovery settings value whether it is a model or a mapping."""
    if isinstance(settings, Mapping):
        return settings
    dump = getattr(settings, "model_dump", None)
    if callable(dump):
        data = dump()
        if isinstance(data, Mapping):
            return data
    return {}


def _select_postings(
    postings: Sequence[DiscoveredPosting],
    resolve: Any,
    budget: int,
) -> tuple[list[tuple[SourceConfig, DiscoveredPosting]], int]:
    """Pick the postings to verify within the budget.

    Index rows are ordered by URL, so truncating the harvested list spends the
    whole budget on one tenant and on boards that were never registered.  The
    selection keeps only resolvable postings and takes them round-robin across
    boards, so one productive tenant cannot starve the rest of the pass.
    """
    groups: dict[str, list[DiscoveredPosting]] = {}
    resolved: dict[str, SourceConfig] = {}
    order: list[str] = []
    unregistered = 0
    for posting in postings:
        source = resolve(posting)
        if source is None:
            unregistered += 1
            continue
        if source.id not in groups:
            groups[source.id] = []
            resolved[source.id] = source
            order.append(source.id)
        groups[source.id].append(posting)

    selected: list[tuple[SourceConfig, DiscoveredPosting]] = []
    round_index = 0
    while len(selected) < budget:
        progressed = False
        for source_id in order:
            group = groups[source_id]
            if round_index < len(group):
                selected.append((resolved[source_id], group[round_index]))
                progressed = True
                if len(selected) >= budget:
                    break
        if not progressed:
            break
        round_index += 1
    return selected, unregistered


def _candidate_row(
    source: DiscoveredSource,
    probe_status: str | None = None,
    postings: int | None = None,
) -> dict[str, object]:
    """A candidate as persisted state (backlog, probation)."""
    observed = postings if postings is not None else source.observed_postings
    row: dict[str, object] = {
        "url": source.url,
        "kind": source.kind,
        "confidence": source.confidence,
    }
    if source.ats_hint:
        row["ats_hint"] = source.ats_hint
    if source.tenant_key:
        row["tenant_key"] = source.tenant_key
    if source.company_hint:
        row["company_hint"] = source.company_hint
    if observed is not None:
        row["postings"] = observed
    if probe_status:
        row["probe_status"] = probe_status
    return row


def _candidate_from_row(row: Mapping[str, object]) -> DiscoveredSource | None:
    url = row.get("url")
    if not isinstance(url, str) or not url:
        return None
    ats_hint = row.get("ats_hint")
    tenant_key = row.get("tenant_key")
    company_hint = row.get("company_hint")
    kind = row.get("kind")
    confidence = row.get("confidence")
    postings = row.get("postings")
    return DiscoveredSource(
        kind=kind if isinstance(kind, str) and kind else "ats",
        url=url,
        company_hint=company_hint if isinstance(company_hint, str) else None,
        ats_hint=ats_hint if isinstance(ats_hint, str) and ats_hint else None,
        confidence=float(confidence) if isinstance(confidence, (int, float)) else 0.6,
        evidence=[],
        tenant_key=tenant_key if isinstance(tenant_key, str) and tenant_key else None,
        observed_postings=(
            postings if isinstance(postings, int) and not isinstance(postings, bool) else None
        ),
    )


def _is_seed_candidate(source: DiscoveredSource) -> bool:
    """Seed shards carry an observed count; live candidates do not."""
    return source.observed_postings is not None


def _fresh_probe(
    cache: Mapping[str, object], ident: str, now: datetime
) -> dict[str, object] | None:
    entry = cache.get(ident)
    if not isinstance(entry, dict):
        return None
    raw_state = entry.get("state")
    ttl = PROBE_TTL_S.get(str(raw_state))
    if ttl is None:
        return None
    moment = _parse_ts(entry.get("at"))
    if moment is None:
        return entry
    if (now - moment).total_seconds() <= ttl:
        return entry
    return None


def _prune_probe_cache(
    cache: Mapping[str, object], now: datetime
) -> dict[str, object]:
    """Keep the probe cache bounded: nothing older than the longest TTL."""
    kept: dict[str, object] = {}
    for key, entry in cache.items():
        if not isinstance(entry, dict):
            continue
        moment = _parse_ts(entry.get("at"))
        if moment is not None and (now - moment).total_seconds() > max(PROBE_TTL_S.values()):
            continue
        kept[key] = entry
    return kept


def _tenant_of_source(source: SourceConfig) -> str:
    """The ATS tenant a source belongs to, for sibling-host matching."""
    if source.ats_tenant_key:
        return source.ats_tenant_key.strip().lower()
    return _tenant_of_url(source.url)


def _tenant_of_url(url: str) -> str:
    """First meaningful path segment of a board URL (the tenant)."""
    parts = [part for part in urlsplit(url.strip()).path.split("/") if part]
    return parts[0].lower() if parts else ""


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


def _rotating_window(
    items: Sequence[SourceConfig], start: int, limit: int
) -> list[SourceConfig]:
    """A bounded, wrapping slice of the registry for per-pass work."""
    if limit <= 0 or not items:
        return []
    if len(items) <= limit:
        return list(items)
    position = start % len(items)
    window = list(items[position : position + limit])
    if len(window) < limit:
        window.extend(items[: limit - len(window)])
    return window


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
