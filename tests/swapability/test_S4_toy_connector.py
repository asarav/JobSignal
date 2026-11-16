"""S4: ToyAtsConnector registered in-test; registry + IPC plugins.list sees it."""

import pytest
from app.application.commands.plugins import PluginsUseCase
from app.application.container import Container
from app.domain.ports.plugins import PluginManifest
from app.plugins.registry import PluginRegistry
from tests.support.fakes import FakeClock, FakeIdGenerator, FakeStructuredLogger
from tests.support.toy_plugins import ToyAtsConnector


class _UowFactory:
    def __call__(self):
        class U:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *a):
                return None
            async def commit(self):
                return None
            async def plugins(self):
                class P:
                    async def list_plugins(self): return []
                return P()
        return U()


def _container(registry: PluginRegistry) -> Container:
    return Container(
        config=None,
        clock=FakeClock(),
        ids=FakeIdGenerator(),
        http=None,
        uow_factory=_UowFactory(),
        events=FakeStructuredLogger(),
        logger=FakeStructuredLogger(),
        registry=registry,
    )


@pytest.mark.asyncio
async def test_toy_connector_registered_in_test_seen_by_registry_and_ipc():
    registry = PluginRegistry()
    connector = ToyAtsConnector()
    registry.add(
        PluginManifest(
            id="ats.toy",
            kind="connector",
            version="1.0.0",
            api_version="1",
            capabilities=frozenset({"list_jobs", "fetch_job"}),
        ),
        connector,
    )
    entries = registry.list_by_kind("connector")
    assert any(e.manifest.id == "ats.toy" for e in entries)

    # Registering a plugin requires no edits to core application code: the
    # generic plugins.list use case exposes it as soon as it is registered.
    uc = PluginsUseCase(_container(registry))
    listed = await uc.list_plugins()
    assert any(plugin["plugin_id"] == "ats.toy" for plugin in listed)

    caps = await uc.capabilities("ats.toy")
    assert "ats.toy" in caps
    assert "list_jobs" in caps["ats.toy"]