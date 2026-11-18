"""M4: auto-discovery registers sources, crawls what is due, keeps its cadence."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

import pytest
from app.application.auto_discover import (
    AUTO_DISCOVER_INTERVAL_S,
    AUTO_DISCOVER_STATE_KEY,
)
from app.application.config import AppConfig
from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.discovery import DiscoveredSource, DiscoveryQuery
from app.domain.ports.plugins import PluginManifest
from app.infrastructure.persistence.memory import InMemoryUnitOfWorkFactory
from app.plugins.discovery import SEEDS_PATH
from app.plugins.discovery.ats_directory import AtsDirectoryProvider
from app.plugins.discovery.web_index import WebIndexProvider
from app.worker.main import DiscoveryWiring, _build_discovery
from tests.support.containers import build_test_container
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger


def _candidate(url: str) -> DiscoveredSource:
    return DiscoveredSource(
        kind="ats",
        url=url,
        company_hint=None,
        ats_hint=None,
        confidence=0.9,
        evidence=[],
    )


def _row(url: str) -> dict[str, str]:
    return {"url": url, "status": "200", "timestamp": "20260203081435"}


class _Provider:
    """A discovery provider yielding a fixed list of career-site URLs."""

    id = "discovery.fake"

    def __init__(self, urls: list[str]) -> None:
        self._urls = urls
        self.calls = 0
        self.cursors: list[object] = []
        self.slices_seen: list[object] = []

    async def discover(
        self, query: DiscoveryQuery, ctx: ExecutionContext
    ) -> AsyncIterator[DiscoveredSource]:
        self.calls += 1
        self.cursors.append(query.extra.get("cursor"))
        self.slices_seen.append(query.extra.get("slices"))
        for url in self._urls:
            yield _candidate(url)


class _BrokenProvider:
    """A provider that fails on its first iteration."""

    id = "discovery.broken"

    async def discover(
        self, query: DiscoveryQuery, ctx: ExecutionContext
    ) -> AsyncIterator[DiscoveredSource]:
        raise RuntimeError("provider exploded")
        yield  # pragma: no cover - keeps this an async generator


def _wire(
    providers: dict[str, Any],
    *,
    clock: FakeClock | None = None,
    factory: InMemoryUnitOfWorkFactory | None = None,
    config: AppConfig | None = None,
) -> tuple[Any, DiscoveryWiring]:
    container = build_test_container(
        http=FakeHttpClient(),
        clock=clock or FakeClock(),
        uow_factory=factory or InMemoryUnitOfWorkFactory(),
        config=config,
    )
    # The bundled providers would use the real index/seed data; tests inject fakes.
    container.registry.set_enabled("discovery.ats_directory", False)
    container.registry.set_enabled("discovery.web_index", False)
    for plugin_id, provider in providers.items():
        container.registry.add(
            PluginManifest(id=plugin_id, kind="discovery", version="1.0.0", api_version="1"),
            provider,
        )
    return container, _build_discovery(container)


@pytest.mark.asyncio
async def test_auto_discover_registers_dedupes_and_crawls():
    first = _Provider(
        [
            "https://jobs.example.com/a",
            "https://jobs.example.com/a/",
            "https://jobs.example.com/b",
        ]
    )
    second = _Provider(
        ["https://jobs.example.com/b", "https://jobs.example.com/c"]
    )
    _, wiring = _wire(
        {"discovery.fake": first, "discovery.fake_two": second},
        clock=FakeClock(),
    )
    # A source the user already added must not be registered twice.
    await wiring.registry.add("https://jobs.example.com/a")

    result = await wiring.auto_discover_use_case.run()

    assert result.discovered == 3
    assert result.registered == 2
    assert result.crawled == 3
    assert result.failed == 0
    assert first.calls == 1
    assert second.calls == 1
    sources = await wiring.registry.list_sources()
    assert len(sources) == 3
    # New sources inherit the configured default cadence (12h) with jitter.
    registered = [s for s in sources if s.url.endswith(("/b", "/c"))]
    assert len(registered) == 2
    for source in registered:
        interval = float(source.schedule["interval_s"])
        assert 0.9 * 12 * 3600 <= interval <= 1.1 * 12 * 3600

    # A second pass discovers the same sites but registers nothing new, and the
    # sources it crawls are the ones that are due (none, right after a crawl).
    again = await wiring.auto_discover_use_case.run()
    assert again.discovered == 3
    assert again.registered == 0
    assert again.crawled == 0
    assert len(await wiring.registry.list_sources()) == 3


@pytest.mark.asyncio
async def test_registration_is_capped_per_pass():
    provider = _Provider([f"https://jobs.example.com/{n}" for n in "abcd"])
    _, wiring = _wire(
        {"discovery.fake": provider},
        config=AppConfig(discovery={"max_new_sources_per_run": 2}),
    )

    result = await wiring.auto_discover_use_case.run()

    assert result.discovered == 4
    assert result.registered == 2
    assert len(await wiring.registry.list_sources()) == 2


@pytest.mark.asyncio
async def test_crawl_budget_is_shared_stalest_first():
    factory = InMemoryUnitOfWorkFactory()
    clock = FakeClock()
    _, wiring = _wire(
        {},
        clock=clock,
        factory=factory,
        config=AppConfig(discovery={"max_crawl_sources_per_run": 1}),
    )
    await wiring.registry.add("https://jobs.example.com/a")
    await wiring.registry.add("https://jobs.example.com/b")

    first = await wiring.auto_discover_use_case.run()
    second = await wiring.auto_discover_use_case.run()

    assert first.crawled == 1
    assert second.crawled == 1
    async with factory() as uow:
        crawled = await uow.crawl_runs.last_started_by_source()
    assert len(crawled) == 2, "the next pass must pick up the source left over"


@pytest.mark.asyncio
async def test_only_due_sources_are_crawled():
    clock = FakeClock()
    _, wiring = _wire({}, clock=clock)
    await wiring.registry.add("https://jobs.example.com/a")

    first = await wiring.auto_discover_use_case.run()
    immediate = await wiring.auto_discover_use_case.run()

    assert first.crawled == 1
    assert immediate.crawled == 0, "a source crawled just now is not due"

    clock.advance(2 * 3600)
    later = await wiring.auto_discover_use_case.run()

    assert later.crawled == 1


@pytest.mark.asyncio
async def test_rotation_cursor_is_passed_and_persisted():
    provider = _Provider([])
    container, wiring = _wire({"discovery.fake": provider})

    await wiring.auto_discover_use_case.run()
    await wiring.auto_discover_use_case.run()

    # Each pass walks `cursor_step` slices, so the cursor advances by 12.
    assert provider.cursors == [0, 12]
    assert provider.slices_seen == [12, 12]
    async with container.uow_factory() as uow:
        state = await uow.settings.get(AUTO_DISCOVER_STATE_KEY)
    assert state is not None
    assert state["rotation_cursor"] == 24


@pytest.mark.asyncio
async def test_auto_discover_skips_disabled_providers():
    provider = _Provider(["https://jobs.example.com/a"])
    container, wiring = _wire({"discovery.fake": provider})
    container.registry.set_enabled("discovery.fake", False)

    result = await wiring.auto_discover_use_case.run()

    assert provider.calls == 0
    assert result.discovered == 0
    assert result.registered == 0


@pytest.mark.asyncio
async def test_a_failing_provider_does_not_stop_the_run():
    container, wiring = _wire(
        {
            "discovery.broken": _BrokenProvider(),
            "discovery.fake": _Provider(["https://jobs.example.com/a"]),
        }
    )

    result = await wiring.auto_discover_use_case.run()

    assert result.discovered == 1
    assert result.registered == 1
    assert result.crawled == 1
    # The failing provider holds the cursor so its slice is retried next pass.
    async with container.uow_factory() as uow:
        state = await uow.settings.get(AUTO_DISCOVER_STATE_KEY)
    assert isinstance(state, dict) and state.get("rotation_cursor") == 0


@pytest.mark.asyncio
async def test_a_source_without_a_connector_is_counted_as_failed():
    container, wiring = _wire({})
    source = await wiring.registry.add("https://jobs.example.com/a")
    async with container.uow_factory() as uow:
        stored = await uow.sources.get(source.id)
        assert stored is not None
        await uow.sources.save(replace(stored, connector_id="ats.missing"))
        await uow.commit()

    result = await wiring.auto_discover_use_case.run()

    assert result.crawled == 0
    assert result.failed == 1


@pytest.mark.asyncio
async def test_daily_cadence_is_due_after_the_interval_and_survives_a_restart():
    clock = FakeClock()
    factory = InMemoryUnitOfWorkFactory()
    container, wiring = _wire({}, clock=clock, factory=factory)
    run_if_due = wiring.auto_discover_use_case.run_if_due
    assert await run_if_due(AUTO_DISCOVER_INTERVAL_S) is not None

    async with container.uow_factory() as uow:
        stored = await uow.settings.get(AUTO_DISCOVER_STATE_KEY)
    assert isinstance(stored, dict) and isinstance(stored.get("last_run_at"), str)

    assert await run_if_due(AUTO_DISCOVER_INTERVAL_S) is None

    # A restarted worker reads the cadence and the cursor from the same database.
    _, restarted = _wire({}, clock=clock, factory=factory)
    assert await restarted.auto_discover_use_case.run_if_due(AUTO_DISCOVER_INTERVAL_S) is None

    clock.advance(AUTO_DISCOVER_INTERVAL_S + 1)
    assert await restarted.auto_discover_use_case.run_if_due(AUTO_DISCOVER_INTERVAL_S) is not None


@pytest.mark.asyncio
async def test_bundled_ats_directory_is_registered_and_yields_seeds():
    container = build_test_container(http=FakeHttpClient())
    entries = container.registry.list_by_kind("discovery")
    ids = {entry.manifest.id for entry in entries}
    assert {"discovery.ats_directory", "discovery.web_index"} <= ids
    assert SEEDS_PATH.is_file()

    provider = AtsDirectoryProvider.from_file(SEEDS_PATH)
    seeds = [seed async for seed in provider.discover(DiscoveryQuery(), _ctx())]
    assert len(seeds) >= 5
    assert all(seed.url.startswith("https://") for seed in seeds)
    assert all(seed.ats_hint is not None for seed in seeds)


@pytest.mark.asyncio
async def test_a_pass_discovers_without_seeds_from_fixture_index_data():
    """The M4 exit criterion: an empty bootstrap plus fixture index data is enough.

    ``discovery.ats_directory`` is disabled (the seed file contributes nothing)
    and the real ``discovery.web_index`` provider is pointed at a stub of the
    public URL index, so the pass must still register and attempt boards.
    """
    import json

    from app.domain.models.http import HttpResponse

    fixture_rows = "".join(
        json.dumps(row) + "\n"
        for row in (
            _row("https://boards.greenhouse.io/acme"),
            _row("https://boards.greenhouse.io/acme/jobs/1"),
            _row("https://boards.greenhouse.io/beta"),
        )
    ).encode()
    collinfo = json.dumps([{"id": "CC-MAIN-2026-43"}]).encode()

    from app.domain.models.common import Ok

    class _IndexStub(FakeHttpClient):
        async def send(self, req):
            self.sent.append(req)
            body = collinfo if "/collinfo" in req.url else fixture_rows
            return Ok(
                HttpResponse(
                    status=200, url=req.url, headers={}, body=body, elapsed_ms=1
                )
            )

    http = _IndexStub()
    container = build_test_container(http=http, clock=FakeClock())
    # Seeds contribute nothing: the bootstrap is empty for this pass.
    container.registry.set_enabled("discovery.ats_directory", False)
    container.registry.set_enabled("discovery.web_index", False)
    container.registry.add(
        PluginManifest(
            id="discovery.web_index_fixture",
            kind="discovery",
            version="1.0.0",
            api_version="1",
        ),
        WebIndexProvider(endpoint="https://index.example/cdx"),
    )
    wiring = _build_discovery(container)
    result = await wiring.auto_discover_use_case.run()

    assert result.discovered == 2
    assert result.registered == 2
    # Every newly registered source was attempted (crawl or a counted failure).
    assert result.crawled + result.failed == 2
    sources = await wiring.registry.list_sources()
    assert {s.url for s in sources} == {
        "https://boards.greenhouse.io/acme",
        "https://boards.greenhouse.io/beta",
    }
    async with container.uow_factory() as uow:
        state = await uow.settings.get(AUTO_DISCOVER_STATE_KEY)
    assert isinstance(state, dict) and state.get("rotation_cursor") == 12


def _ctx() -> ExecutionContext:
    return ExecutionContext(
        task_id="test",
        clock=FakeClock(),
        http=FakeHttpClient(),
        logger=FakeStructuredLogger(),
        cancel=CancellationToken(),
        deadline=None,
        config={},
    )
