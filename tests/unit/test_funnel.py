"""M6: relevance-ranked discovery funnel (spec sec 6.8, tests sec 19.10)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.application.auto_discover import AutoDiscoverUseCase, _select_postings
from app.application.config import AppConfig
from app.application.sources import run_crawl, verify_posting
from app.domain.models.candidate import SearchProfile
from app.domain.models.common import Ok
from app.domain.models.context import ExecutionContext
from app.domain.models.discovery import DiscoveredPosting, DiscoveredSource, DiscoveryQuery
from app.domain.models.http import HttpResponse
from app.domain.models.job import RawJob, RawJobRef
from app.domain.models.source import SourceConfig
from app.domain.ports.plugins import PluginManifest
from app.domain.services.normalizer import content_hash_for, normalize_job
from app.domain.services.tiers import (
    DEFAULT_TIER_INTERVALS_S,
    TIER_COLD,
    TIER_HOT,
    TIER_WARM,
    assign_tier,
    interval_for_tier,
)
from app.worker.main import _build_discovery
from tests.support.containers import build_test_container
from tests.support.fakes import FakeClock, FakeHttpClient
from tests.unit.test_auto_discover import _Provider, _wire

BOARD = "https://jobs.example.com/acme"


def _board(url: str = BOARD) -> DiscoveredSource:
    return DiscoveredSource(
        kind="career_site",
        url=url,
        company_hint="Acme",
        ats_hint=None,
        confidence=0.9,
        evidence=[],
    )


class _PostingProvider:
    """A funnel provider that yields fixed posting candidates."""

    id = "discovery.funnel_fake"

    def __init__(self, postings: list[DiscoveredPosting]) -> None:
        self._postings = postings
        self.calls = 0

    async def discover(
        self, query: DiscoveryQuery, ctx: ExecutionContext
    ) -> AsyncIterator[DiscoveredSource]:
        return
        yield  # pragma: no cover

    async def discover_postings(
        self, query: DiscoveryQuery, ctx: ExecutionContext
    ) -> AsyncIterator[DiscoveredPosting]:
        self.calls += 1
        for posting in self._postings:
            yield posting


async def _seed_profile(container: Any, *, title_include: str = "engineer") -> None:
    async with container.uow_factory() as uow:
        await uow.resumes.add_profile("default", "Default")
        await uow.resumes.update_overrides(
            "default",
            {
                "skills_added": ["python", "sql"],
                "preferences": {"titles": ["python engineer"]},
            },
        )
        await uow.search_profiles.add(
            SearchProfile(
                id="sp_1",
                name="Backend",
                candidate_profile_id="default",
                hard_filters={"title_include": title_include},
            )
        )
        await uow.commit()


async def _seed_job(
    container: Any,
    source: Any,
    *,
    job_id: str = "job_seed",
    title: str = "Senior Python Engineer",
    description: str = "python sql",
    url_suffix: str = "1",
) -> None:
    async with container.uow_factory() as uow:
        url = f"{source.url.rstrip('/')}/{url_suffix}"
        raw = RawJob(
            ref=RawJobRef(source_id=source.id, url=url),
            title=title,
            body_text=description,
            body_html=None,
            payload={"description": description},
        )
        job = normalize_job(
            raw, source, company_name="Acme", now=container.clock.now()
        )
        await uow.jobs.upsert(
            job_id,
            company_id="co_acme",
            source_id=source.id,
            job=job,
            content_hash=content_hash_for(title, description),
        )
        await uow.commit()


def test_tier_assignment_is_hot_warm_cold_and_explained() -> None:
    hot = assign_tier(
        [{"title": "Senior Python Engineer", "description_text": "python sql"}],
        skills=["python", "sql"],
        titles=["python engineer"],
        filter_sets=[{"title_include": "engineer"}],
    )
    assert hot.tier == TIER_HOT
    assert "target titles" in hot.reason and "python" in hot.reason

    warm = assign_tier(
        [{"title": "Backend Engineer", "description_text": "python services"}],
        skills=["python", "sql"],
        titles=["python engineer"],
        filter_sets=[{"title_include": "engineer"}],
    )
    assert warm.tier == TIER_WARM
    assert "skills" in warm.reason

    cold = assign_tier(
        [{"title": "Accountant", "description_text": "ledgers"}],
        skills=["python", "sql"],
        titles=["python engineer"],
        filter_sets=[{"title_include": "engineer"}],
    )
    assert cold.tier == TIER_COLD
    assert "hard filters" in cold.reason


def test_tier_assignment_respects_hard_filters() -> None:
    decision = assign_tier(
        [{"title": "Senior Python Engineer", "description_text": "python sql"}],
        skills=["python", "sql"],
        titles=["python engineer"],
        filter_sets=[{"title_include": "marketing"}],
    )
    assert decision.tier == TIER_COLD
    assert "hard filters" in decision.reason


def test_tier_intervals_default_and_custom() -> None:
    assert interval_for_tier(TIER_HOT) == DEFAULT_TIER_INTERVALS_S[0]
    assert interval_for_tier(TIER_WARM) == DEFAULT_TIER_INTERVALS_S[1]
    assert interval_for_tier(TIER_COLD) == DEFAULT_TIER_INTERVALS_S[2]
    assert interval_for_tier("unknown", (10, 20, 30)) == 30


@pytest.mark.asyncio
async def test_a_tier_never_crawls_more_often_than_its_interval() -> None:
    clock = FakeClock()
    container, wiring = _wire(
        {},
        clock=clock,
        config=AppConfig(discovery={"tier_intervals_s": [3600, 86400, 604800]}),
    )
    await _seed_profile(container)
    source = await wiring.registry.add(f"{BOARD}/")
    await _seed_job(container, source)

    first = await wiring.auto_discover_use_case.run()
    assert first.crawled == 1
    stored = await wiring.registry.get(source.id)
    assert stored is not None and stored.schedule.get("tier") == TIER_HOT

    clock.advance(1800)
    too_soon = await wiring.auto_discover_use_case.run()
    assert too_soon.crawled == 0, "a hot source still respects its 1h interval"

    clock.advance(1900)
    due_again = await wiring.auto_discover_use_case.run()
    assert due_again.crawled == 1


@pytest.mark.asyncio
async def test_tier_reassignment_after_a_profile_change_is_evented() -> None:
    clock = FakeClock()
    container, wiring = _wire({}, clock=clock)
    await _seed_profile(container)
    source = await wiring.registry.add(f"{BOARD}/")
    await _seed_job(container, source)

    await wiring.auto_discover_use_case.run()
    stored = await wiring.registry.get(source.id)
    assert stored is not None and stored.schedule.get("tier") == TIER_HOT

    async with container.uow_factory() as uow:
        profile = await uow.search_profiles.get("sp_1")
        assert profile is not None
        await uow.search_profiles.save(
            SearchProfile(
                id=profile.id,
                name=profile.name,
                candidate_profile_id=profile.candidate_profile_id,
                hard_filters={"title_include": "marketing"},
                preferences=profile.preferences,
            )
        )
        await uow.commit()

    clock.advance(4000)
    result = await wiring.auto_discover_use_case.run()

    assert result.tiers_changed == 1
    changed = [
        event
        for event in container.events.events
        if event["topic"] == "discovery.tier_changed"
    ]
    assert changed, "a tier change must be evented"
    payload = changed[-1]["payload"]
    assert payload["source_id"] == source.id
    assert payload["from"] == TIER_HOT and payload["to"] == TIER_COLD
    assert "hard filters" in payload["reason"]
    stored = await wiring.registry.get(source.id)
    assert stored is not None and stored.schedule.get("tier") == TIER_COLD


@pytest.mark.asyncio
async def test_profile_change_retiers_without_waiting_for_a_pass() -> None:
    """The IPC hook must retier immediately, not on the next discovery pass."""
    clock = FakeClock()
    container, wiring = _wire({}, clock=clock)
    await _seed_profile(container)
    source = await wiring.registry.add(f"{BOARD}/")
    await _seed_job(container, source)
    await wiring.auto_discover_use_case.run()

    async with container.uow_factory() as uow:
        profile = await uow.search_profiles.get("sp_1")
        assert profile is not None
        await uow.search_profiles.save(
            SearchProfile(
                id=profile.id,
                name=profile.name,
                candidate_profile_id=profile.candidate_profile_id,
                hard_filters={"title_include": "marketing"},
                preferences=profile.preferences,
            )
        )
        await uow.commit()

    # No clock advance and no pass: a profile edit alone retiers the source.
    changed = await wiring.retier()
    assert changed == 1
    stored = await wiring.registry.get(source.id)
    assert stored is not None and stored.schedule.get("tier") == TIER_COLD


@pytest.mark.asyncio
async def test_disabling_the_funnel_restores_plain_due_based_crawling() -> None:
    clock = FakeClock()
    posting = DiscoveredPosting(
        url=f"{BOARD}/1", board_url=BOARD, confidence=0.6
    )
    container, wiring = _wire(
        {"discovery.funnel_fake": _PostingProvider([posting])},
        clock=clock,
        config=AppConfig(discovery={"funnel_enabled": False}),
    )
    await _seed_profile(container)
    source = await wiring.registry.add(f"{BOARD}/")

    first = await wiring.auto_discover_use_case.run()

    assert first.postings_harvested == 0
    assert first.tiers_changed == 0
    stored = await wiring.registry.get(source.id)
    assert stored is not None and stored.schedule.get("tier") is None
    assert first.crawled == 1
    assert source.schedule.get("tier") is None

    clock.advance(2 * 3600)
    later = await wiring.auto_discover_use_case.run()
    assert later.crawled == 1, "the plain 1h source interval still applies"


class _GreenhouseHttp(FakeHttpClient):
    def __init__(self) -> None:
        super().__init__()
        self.jobs = [
            {
                "id": 123,
                "title": "Senior Python Engineer",
                "absolute_url": "https://boards.greenhouse.io/acme/jobs/123",
                "updated_at": "2026-01-01T00:00:00Z",
                "content": "python sql",
            }
        ]

    async def send(self, req: Any) -> Any:
        self.sent.append(req)
        if req.url.endswith("/jobs"):
            body = json.dumps({"jobs": self.jobs}).encode()
        elif req.url.endswith("/jobs/123"):
            body = json.dumps(self.jobs[0]).encode()
        else:
            body = b"ok"
        return Ok(
            HttpResponse(status=200, url=req.url, headers={}, body=body, elapsed_ms=1)
        )


@pytest.mark.asyncio
async def test_harvested_posting_is_verified_with_one_fetch() -> None:
    http = _GreenhouseHttp()
    container = build_test_container(http=http, clock=FakeClock())
    from app.application.sources import SourceRegistry
    from app.plugins.ats.greenhouse import GreenhouseConnector

    registry = SourceRegistry(container.uow_factory, [])
    board = await registry.add(
        "https://boards.greenhouse.io/acme",
        connector_id="ats.greenhouse",
        tenant_key="acme",
    )
    connector = GreenhouseConnector()
    before = len(http.sent)
    outcome = await verify_posting(
        source=board,
        connector=connector,
        url="https://boards.greenhouse.io/acme/jobs/123",
        source_posting_id="123",
        uow_factory=container.uow_factory,
        http=http,
        clock_now=container.clock.now,
        events=container.events,
    )

    assert outcome == "new"
    assert len(http.sent) - before == 1, "verification fetches the posting once"
    async with container.uow_factory() as uow:
        jobs = await uow.jobs.list()
        assert len(jobs) == 1
        provenance = jobs[0].get("provenance")
        assert isinstance(provenance, list)
        assert any("via_index" in entry.get("notes", ()) for entry in provenance)


@pytest.mark.asyncio
async def test_harvested_posting_dedupes_against_a_full_listing() -> None:
    http = _GreenhouseHttp()
    container = build_test_container(http=http, clock=FakeClock())
    from app.application.sources import SourceRegistry
    from app.plugins.ats.greenhouse import GreenhouseConnector

    registry = SourceRegistry(container.uow_factory, [])
    source = await registry.add(
        "https://boards.greenhouse.io/acme",
        connector_id="ats.greenhouse",
        tenant_key="acme",
    )
    connector = GreenhouseConnector()
    await run_crawl(
        source=source,
        connector=connector,
        uow_factory=container.uow_factory,
        http=http,
        clock_now=container.clock.now,
        events=container.events,
    )
    outcome = await verify_posting(
        source=source,
        connector=connector,
        url="https://boards.greenhouse.io/acme/jobs/123",
        source_posting_id="123",
        uow_factory=container.uow_factory,
        http=http,
        clock_now=container.clock.now,
    )
    assert outcome == "skipped"
    async with container.uow_factory() as uow:
        assert len(await uow.jobs.list()) == 1


@pytest.mark.asyncio
async def test_harvested_postings_never_mark_jobs_removed() -> None:
    clock = FakeClock()
    posting = DiscoveredPosting(url=f"{BOARD}/1", board_url=BOARD, confidence=0.6)
    container, wiring = _wire(
        {"discovery.funnel_fake": _PostingProvider([posting])}, clock=clock
    )
    await _seed_profile(container)
    source = await wiring.registry.add(f"{BOARD}/")
    await _seed_job(container, source)
    async with container.uow_factory() as uow:
        await uow.crawl_runs.create("run_seed", source.id, clock.now())
        await uow.commit()

    result = await wiring.auto_discover_use_case.run()

    assert result.postings_verified == 1
    async with container.uow_factory() as uow:
        jobs = await uow.jobs.list(source_id=source.id)
        assert jobs and all(job.get("posting_status") == "active" for job in jobs)
        for job in jobs:
            observations = await uow.observations.list_for_job(str(job["id"]))
            assert all(obs.get("posting_status") != "not_seen" for obs in observations)


@pytest.mark.asyncio
async def test_funnel_verification_is_capped_per_pass() -> None:
    postings = [
        DiscoveredPosting(url=f"{BOARD}/{n}", board_url=BOARD, confidence=0.6)
        for n in range(1, 4)
    ]
    container, wiring = _wire(
        {
            "discovery.board": _Provider([f"{BOARD}/"]),
            "discovery.funnel_fake": _PostingProvider(postings),
        },
        config=AppConfig(discovery={"funnel_postings_per_pass": 2}),
    )

    result = await wiring.auto_discover_use_case.run()

    assert result.postings_harvested == 3
    assert result.postings_verified == 2
    async with container.uow_factory() as uow:
        rows = await uow.jobs.list()
    via_index = [
        row
        for row in rows
        if any(
            "via_index" in entry.get("notes", ())
            for entry in row.get("provenance", [])
            if isinstance(entry, dict)
        )
    ]
    assert len(via_index) == 2


def test_funnel_budget_is_spread_across_boards_and_skips_unregistered() -> None:
    """Index rows are URL-ordered: truncating would spend the pass on one tenant."""
    board_a = SourceConfig(id="src_a", url="https://jobs.example.com/a", kind="career_site")
    board_b = SourceConfig(id="src_b", url="https://jobs.example.com/b", kind="career_site")
    by_board = {board_a.url: board_a, board_b.url: board_b}

    def posting(url: str, board: str) -> DiscoveredPosting:
        return DiscoveredPosting(url=url, board_url=board, confidence=0.6)

    postings = [
        posting("https://jobs.example.com/a/1", board_a.url),
        posting("https://jobs.example.com/a/2", board_a.url),
        posting("https://jobs.example.com/a/3", board_a.url),
        posting("https://jobs.example.com/b/1", board_b.url),
        posting("https://jobs.example.com/b/2", board_b.url),
        posting("https://jobs.example.com/z/1", "https://jobs.example.com/z"),
    ]

    selected, unregistered = _select_postings(
        postings, lambda candidate: by_board.get(candidate.board_url), 4
    )

    assert unregistered == 1, "a board that was never registered cannot be verified"
    assert [(source.id, candidate.url) for source, candidate in selected] == [
        ("src_a", "https://jobs.example.com/a/1"),
        ("src_b", "https://jobs.example.com/b/1"),
        ("src_a", "https://jobs.example.com/a/2"),
        ("src_b", "https://jobs.example.com/b/2"),
    ]


def _isolated_wiring(http: Any, providers: dict[str, Any]) -> tuple[Any, Any]:
    """A wiring where only the injected providers are enabled."""
    container = build_test_container(http=http, clock=FakeClock())
    for plugin_id in (
        "discovery.ats_directory",
        "discovery.web_index",
        "discovery.domain_index",
        "discovery.oracle_recruiting",
        "discovery.funnel",
    ):
        container.registry.set_enabled(plugin_id, False)
    for plugin_id, provider in providers.items():
        container.registry.add(
            PluginManifest(id=plugin_id, kind="discovery", version="1.0.0", api_version="1"),
            provider,
        )
    return container, _build_discovery(container)


@pytest.mark.asyncio
async def test_posting_on_a_sibling_host_resolves_to_the_registered_board() -> None:
    """Greenhouse serves boards on two hosts; a board is registered on one of them."""
    http = _GreenhouseHttp()
    posting = DiscoveredPosting(
        url="https://boards.greenhouse.io/acme/jobs/123",
        board_url="https://boards.greenhouse.io/acme",
        connector_id="ats.greenhouse",
        source_posting_id="123",
        tenant_key="acme",
    )
    container, wiring = _isolated_wiring(
        http, {"discovery.funnel_fake": _PostingProvider([posting])}
    )
    source = await wiring.registry.add(
        "https://job-boards.greenhouse.io/acme",
        connector_id="ats.greenhouse",
        tenant_key="acme",
        interval_s=43200,
    )
    # A crawl run just now keeps the board out of this pass, so only the funnel
    # can produce the job.
    async with container.uow_factory() as uow:
        await uow.crawl_runs.create("run_seed", source.id, container.clock.now())
        await uow.commit()

    result = await wiring.auto_discover_use_case.run()

    assert result.crawled == 0
    assert result.postings_verified == 1
    async with container.uow_factory() as uow:
        rows = await uow.jobs.list()
    assert len(rows) == 1
    assert rows[0]["title"] == "Senior Python Engineer"
    assert any(
        "via_index" in (entry.get("notes") or []) for entry in rows[0]["provenance"]
    )


@pytest.mark.asyncio
async def test_a_full_listing_clears_the_via_index_marker() -> None:
    http = _GreenhouseHttp()
    container = build_test_container(http=http, clock=FakeClock())
    from app.application.sources import SourceRegistry
    from app.plugins.ats.greenhouse import GreenhouseConnector

    registry = SourceRegistry(container.uow_factory, [])
    source = await registry.add(
        "https://boards.greenhouse.io/acme",
        connector_id="ats.greenhouse",
        tenant_key="acme",
    )
    connector = GreenhouseConnector()
    assert (
        await verify_posting(
            source=source,
            connector=connector,
            url="https://boards.greenhouse.io/acme/jobs/123",
            source_posting_id="123",
            uow_factory=container.uow_factory,
            http=http,
            clock_now=container.clock.now,
        )
        == "new"
    )

    stats = await run_crawl(
        source=source,
        connector=connector,
        uow_factory=container.uow_factory,
        http=http,
        clock_now=container.clock.now,
    )

    assert stats["skipped"] == 1, "the listing found the same content"
    async with container.uow_factory() as uow:
        rows = await uow.jobs.list()
    assert len(rows) == 1
    assert not any(
        "via_index" in (entry.get("notes") or []) for entry in rows[0]["provenance"]
    ), "a full listing is the confirmation the badge waits for"


@pytest.mark.asyncio
async def test_tiers_are_cleared_when_profile_signals_disappear() -> None:
    clock = FakeClock()
    container, wiring = _wire({}, clock=clock)
    await _seed_profile(container)
    source = await wiring.registry.add(f"{BOARD}/")
    await _seed_job(container, source)

    await wiring.auto_discover_use_case.run()
    stored = await wiring.registry.get(source.id)
    assert stored is not None and stored.schedule.get("tier") == TIER_HOT

    # The profile is effectively cleared: no overridden skills, no titles and
    # no search profiles with hard filters remain.
    async with container.uow_factory() as uow:
        await uow.resumes.update_overrides("default", {})
        for profile in await uow.search_profiles.list():
            await uow.search_profiles.delete(profile.id)
        await uow.commit()

    result = await wiring.auto_discover_use_case.run()

    assert result.tiers_changed == 1
    stored = await wiring.registry.get(source.id)
    assert stored is not None
    assert "tier" not in stored.schedule
    assert "tier_reason" not in stored.schedule
    cleared = [
        event
        for event in container.events.events
        if event["topic"] == "discovery.tier_changed" and event["payload"].get("to") is None
    ]
    assert cleared, "losing a tier is a transition the UI must hear about"
    assert cleared[-1]["payload"]["from"] == TIER_HOT


class _CountingGate(asyncio.Semaphore):
    """The shared crawl gate, recording how often it is entered."""

    def __init__(self) -> None:
        super().__init__(1)
        self.enters = 0
        self.active = 0
        self.max_active = 0

    async def __aenter__(self) -> None:
        self.enters += 1
        await super().__aenter__()
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        return None

    async def __aexit__(self, *exc: object) -> None:
        self.active -= 1
        await super().__aexit__(*exc)  # type: ignore[arg-type]


class _MultiPostingHttp(FakeHttpClient):
    """Serves several Greenhouse postings so verifications can overlap."""

    def __init__(self) -> None:
        super().__init__()
        self.jobs = {
            str(posting_id): {
                "id": posting_id,
                "title": f"Senior Python Engineer {posting_id}",
                "absolute_url": f"{BOARD}/jobs/{posting_id}",
                "updated_at": "2026-01-01T00:00:00Z",
                "content": "python sql",
            }
            for posting_id in (123, 456, 789)
        }

    async def send(self, req: Any) -> Any:
        self.sent.append(req)
        await asyncio.sleep(0.01)
        posting_id = req.url.rstrip("/").rsplit("/", 1)[-1]
        body = json.dumps(self.jobs.get(posting_id, {})).encode()
        return Ok(
            HttpResponse(status=200, url=req.url, headers={}, body=body, elapsed_ms=1)
        )


@pytest.mark.asyncio
async def test_funnel_verifications_share_the_global_crawl_gate() -> None:
    """Section 19.10: a funnel pass never exceeds the global crawl gate."""
    http = _MultiPostingHttp()
    container = build_test_container(http=http, clock=FakeClock())
    for plugin_id in (
        "discovery.ats_directory",
        "discovery.web_index",
        "discovery.domain_index",
        "discovery.oracle_recruiting",
        "discovery.funnel",
    ):
        container.registry.set_enabled(plugin_id, False)
    postings = [
        DiscoveredPosting(
            url=f"{BOARD}/jobs/{posting_id}",
            board_url=BOARD,
            connector_id="ats.greenhouse",
            source_posting_id=str(posting_id),
            tenant_key="acme",
            confidence=0.6,
        )
        for posting_id in (123, 456, 789)
    ]
    container.registry.add(
        PluginManifest(
            id="discovery.funnel_fake", kind="discovery", version="1.0.0", api_version="1"
        ),
        _PostingProvider(postings),
    )
    wiring = _build_discovery(container)
    source = await wiring.registry.add(
        "https://boards.greenhouse.io/acme",
        connector_id="ats.greenhouse",
        tenant_key="acme",
        interval_s=43200,
    )
    # Crawled just now, so only the funnel verifications can run in this pass.
    async with container.uow_factory() as uow:
        await uow.crawl_runs.create("run_seed", source.id, container.clock.now())
        await uow.commit()

    gate = _CountingGate()
    use_case = AutoDiscoverUseCase(container, wiring.registry, http, crawl_gate=gate)
    result = await use_case.run()

    assert result.postings_verified == 3
    assert gate.enters == 3, "every verification holds the shared crawl gate"
    assert gate.max_active == 1, "the gate bounds funnel fetches like any crawl"
