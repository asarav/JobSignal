"""plugins.* handlers."""

from __future__ import annotations

from app.application.commands.plugins import PluginsUseCase
from app.application.dto import (
    PluginConfigureParams,
    PluginIdParams,
    PluginOptionalIdParams,
)


class PluginsHandlers:
    def __init__(self, plugins: PluginsUseCase) -> None:
        self._plugins = plugins

    async def list(self, params: dict) -> dict:
        return {"plugins": [dict(p) for p in await self._plugins.list_plugins()]}

    async def enable(self, params: PluginIdParams) -> dict:
        await self._plugins.enable(params)
        return {"ok": True}

    async def disable(self, params: PluginIdParams) -> dict:
        await self._plugins.disable(params)
        return {"ok": True}

    async def configure(self, params: PluginConfigureParams) -> dict:
        return {"config": dict(await self._plugins.configure(params))}

    async def get_capabilities(self, params: PluginOptionalIdParams) -> dict:
        return {"capabilities": await self._plugins.capabilities(params.plugin_id)}

    async def reset_circuit(self, params: PluginIdParams) -> dict:
        await self._plugins.reset_circuit(params)
        return {"ok": True}
