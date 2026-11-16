"""Unit-of-work backed plugin state adapter.

The registry needs plugin state across the lifetime of the worker, while each
SQLite unit of work owns a short-lived connection. This adapter opens a fresh
unit of work per operation and commits it, keeping the registry independent of
connection details.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.domain.ports.repositories import UnitOfWorkFactory


class UowPluginStateRepository:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def upsert_plugin(
        self,
        plugin_id: str,
        kind: str,
        version: str,
        api_version: str,
        *,
        enabled: bool = True,
        health: Mapping[str, object] | None = None,
    ) -> None:
        async with self._uow_factory() as uow:
            await uow.plugins.upsert_plugin(
                plugin_id,
                kind,
                version,
                api_version,
                enabled=enabled,
                health=health,
            )
            await uow.commit()

    async def list_plugins(self) -> Sequence[Mapping[str, object]]:
        async with self._uow_factory() as uow:
            return await uow.plugins.list_plugins()

    async def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        async with self._uow_factory() as uow:
            await uow.plugins.set_enabled(plugin_id, enabled)
            await uow.commit()

    async def get_health(self, plugin_id: str) -> Mapping[str, object]:
        async with self._uow_factory() as uow:
            return await uow.plugins.get_health(plugin_id)

    async def set_health(self, plugin_id: str, health: Mapping[str, object]) -> None:
        async with self._uow_factory() as uow:
            await uow.plugins.set_health(plugin_id, health)
            await uow.commit()

    async def get_config(self, plugin_id: str) -> Mapping[str, object] | None:
        async with self._uow_factory() as uow:
            return await uow.plugins.get_config(plugin_id)

    async def set_config(self, plugin_id: str, config: Mapping[str, object]) -> None:
        async with self._uow_factory() as uow:
            await uow.plugins.set_config(plugin_id, config)
            await uow.commit()


def make_plugin_state_repository(uow_factory: UnitOfWorkFactory) -> UowPluginStateRepository:
    return UowPluginStateRepository(uow_factory)
