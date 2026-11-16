"""S8: isolation shim - one toy connector timeout doesn't break second call; breaker opens."""

import asyncio

import pytest
from app.plugins.isolation import CircuitBreaker, IsolationShim, PortError
from tests.support.fakes import FakeStructuredLogger


async def _slow(*a, **k):
    await asyncio.sleep(10)
    return "done"


@pytest.mark.asyncio
async def test_isolation_timeout_does_not_break_second_call():
    logger = FakeStructuredLogger()
    breaker = CircuitBreaker(consecutive_failures=3)
    shim = IsolationShim(breaker, timeout_s=0.05, logger=logger)

    with pytest.raises(PortError) as exc:
        await shim.call(_slow)
    assert exc.value.code == "plugin.timeout"

    async def _ok():
        await asyncio.sleep(0)
        return "ok"

    # A second call immediately after should still be allowed (breaker not open yet from 1 failure)
    result = await shim.call(_ok)
    assert result == "ok"


@pytest.mark.asyncio
async def test_breaker_opens_after_threshold():
    logger = FakeStructuredLogger()
    breaker = CircuitBreaker(consecutive_failures=2)
    shim = IsolationShim(breaker, timeout_s=0.05, logger=logger)

    for _ in range(2):
        with pytest.raises(PortError):
            await shim.call(_slow)
    assert breaker.state == "open"
    with pytest.raises(PortError) as exc:
        await shim.call(_slow)
    assert exc.value.code == "plugin.circuit_open"