"""Contract: the plugin registry enforces isolation and identity rules."""

from __future__ import annotations

import pytest
from app.domain.models.common import PortError
from app.domain.ports.plugins import PluginManifest
from app.plugins.isolation import CircuitBreaker, CircuitState, IsolationShim
from app.plugins.registry import PluginRegistry, PluginRegistryError
from tests.support.fakes import FakeStructuredLogger


def _manifest(plugin_id: str, *, kind: str = "connector", api_version: str = "1"):
    return PluginManifest(
        id=plugin_id,
        kind=kind,
        version="1.0.0",
        api_version=api_version,
        capabilities=frozenset({"list_jobs"}),
    )


def test_duplicate_ids_are_rejected():
    registry = PluginRegistry()
    registry.add(_manifest("ats.one"), object())
    with pytest.raises(PluginRegistryError):
        registry.add(_manifest("ats.one"), object())


def test_duplicate_ids_across_kinds_are_rejected():
    registry = PluginRegistry()
    registry.add(_manifest("same.id", kind="connector"), object())
    with pytest.raises(PluginRegistryError):
        registry.add(_manifest("same.id", kind="extraction"), object())


def test_incompatible_api_version_is_rejected():
    registry = PluginRegistry()
    with pytest.raises(PluginRegistryError):
        registry.add(_manifest("ats.future", api_version="2"), object())


def test_registry_capabilities_and_state():
    registry = PluginRegistry()
    registry.add(_manifest("ats.one"), object())
    assert registry.capabilities("ats.one") == {
        "ats.one": frozenset({"list_jobs"})
    }
    registry.set_enabled("ats.one", False)
    assert registry.is_enabled("ats.one") is False
    described = registry.describe()
    assert described[0]["plugin_id"] == "ats.one"
    assert described[0]["enabled"] is False


@pytest.mark.asyncio
async def test_health_is_persisted_through_the_on_change_hook():
    saved: dict[str, dict] = {}

    def persist(health: dict) -> None:
        saved.update(health)

    async def failing() -> None:
        raise RuntimeError("boom")

    breaker = CircuitBreaker(consecutive_failures=1)
    shim = IsolationShim(
        breaker, timeout_s=1, logger=FakeStructuredLogger(), on_change=persist
    )
    with pytest.raises(PortError):
        await shim.call(failing)
    assert breaker.state == CircuitState.OPEN
    assert saved["state"] == CircuitState.OPEN
