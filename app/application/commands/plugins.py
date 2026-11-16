"""Plugin enable/disable/configure/capabilities/reset_circuit use cases."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.application.container import Container
from app.application.dto import PluginConfigureParams, PluginIdParams
from app.domain.models.common import PortError
from app.domain.ports.plugins import PluginRegistryPort


class PluginsUseCase:
    def __init__(self, container: Container) -> None:
        self._container = container

    def _registry(self) -> PluginRegistryPort:
        if self._container.registry is None:
            raise PortError(code="plugin.unavailable", message="plugin registry is not configured")
        return self._container.registry

    async def list_plugins(self) -> Sequence[Mapping[str, Any]]:
        registry = self._registry()
        await registry.persist_state()
        return registry.describe()

    async def enable(self, params: PluginIdParams) -> None:
        await self._set_enabled(params.plugin_id, True)

    async def disable(self, params: PluginIdParams) -> None:
        await self._set_enabled(params.plugin_id, False)

    async def _set_enabled(self, plugin_id: str, enabled: bool) -> None:
        registry = self._registry()
        registry.require(plugin_id)
        registry.set_enabled(plugin_id, enabled)
        await registry.persist_state()

    async def configure(self, params: PluginConfigureParams) -> Mapping[str, Any]:
        registry = self._registry()
        entry = registry.require(params.plugin_id)
        config = dict(params.config)
        schema = entry.manifest.config_schema
        if schema is not None:
            validated = schema.model_validate(config)
            config = validated.model_dump(mode="json")
        async with self._container.uow_factory() as uow:
            await uow.plugins.set_config(params.plugin_id, config)
            await uow.commit()
            return await uow.plugins.get_config(params.plugin_id) or {}

    async def capabilities(self, plugin_id: str | None = None) -> dict[str, list[str]]:
        registry = self._registry()
        raw = registry.capabilities(plugin_id)
        return {pid: sorted(caps) for pid, caps in raw.items()}

    async def reset_circuit(self, params: PluginIdParams) -> None:
        registry = self._registry()
        registry.reset_circuit(params.plugin_id)
        await registry.persist_state()
