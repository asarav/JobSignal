"""M4: the bundled seed file feeds discovery, including explicit tenant keys."""

from __future__ import annotations

from app.domain.models.context import CancellationToken, ExecutionContext
from app.domain.models.discovery import DiscoveryQuery
from app.plugins.discovery.ats_directory import AtsDirectoryProvider
from tests.support.fakes import FakeClock, FakeHttpClient, FakeStructuredLogger


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


async def test_seed_rows_keep_their_tenant_key() -> None:
    """Workday seeds carry the tenant key; the other hosts leave it unset."""
    provider = AtsDirectoryProvider(
        seeds=[
            {
                "url": "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers",
                "company_hint": "Acme",
                "ats_hint": "ats.workday",
                "tenant_key": "acme",
                "confidence": 0.9,
            },
            {
                "url": "https://boards.greenhouse.io/stripe",
                "company_hint": "Stripe",
                "ats_hint": "ats.greenhouse",
                "confidence": 0.9,
            },
        ]
    )

    seeds = [seed async for seed in provider.discover(DiscoveryQuery(), _ctx())]

    assert [seed.tenant_key for seed in seeds] == ["acme", None]
    assert seeds[0].ats_hint == "ats.workday"
    assert seeds[1].url == "https://boards.greenhouse.io/stripe"


async def test_malformed_tenant_key_is_ignored() -> None:
    provider = AtsDirectoryProvider(
        seeds=[
            {
                "url": "https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers",
                "tenant_key": "",
            }
        ]
    )

    (seed,) = [seed async for seed in provider.discover(DiscoveryQuery(), _ctx())]
    assert seed.tenant_key is None
