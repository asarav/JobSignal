"""Plugin registry: registration, ordering, state, and capability exposure."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from app.domain.models.common import PortError
from app.domain.ports.plugins import PluginManifest
from app.plugins.isolation import CircuitBreaker, IsolationShim

if TYPE_CHECKING:
    from app.domain.ports.logging import StructuredLogger
    from app.domain.ports.repositories import PluginStateRepository


class PluginRegistryError(Exception):
    """Raised for duplicate IDs or incompatible plugin API versions."""


@dataclass
class PluginEntry:
    manifest: PluginManifest
    instance: Any
    shim: IsolationShim | None = None


@dataclass
class PluginDeps:
    """Dependencies injected into ``register(registry, deps)``."""

    uow_factory: Any
    clock: Any
    http: Any
    logger: StructuredLogger | None
    config: Any
    browser: Any = None


class PluginRegistry:
    """Registry for all plugin kinds.

    Registration is by ``(kind, id)``. Duplicate IDs and incompatible
    ``api_version`` values are rejected. Enabled state and circuit health are
    persisted through a ``PluginStateRepository`` when one is supplied.
    """

    SUPPORTED_API_VERSIONS = frozenset({"1"})

    def __init__(
        self,
        state_repo: PluginStateRepository | None = None,
        *,
        logger: StructuredLogger | None = None,
        default_timeout_s: float = 30.0,
    ) -> None:
        self._state_repo = state_repo
        self._logger = logger
        self._default_timeout_s = default_timeout_s
        self._entries: dict[tuple[str, str], PluginEntry] = {}
        self._breakers: dict[str, CircuitBreaker] = {}
        self._enabled: dict[str, bool] = {}

    @property
    def state_repo(self) -> PluginStateRepository | None:
        return self._state_repo

    def add(self, manifest: PluginManifest, instance: Any, *, enabled: bool = True) -> None:
        if manifest.api_version not in self.SUPPORTED_API_VERSIONS:
            raise PluginRegistryError(
                f"plugin {manifest.id} targets unsupported api_version {manifest.api_version!r}"
            )
        if any(pid == manifest.id for (_kind, pid) in self._entries):
            raise PluginRegistryError(f"duplicate plugin registration: {manifest.id}")
        key = (manifest.kind, manifest.id)
        self._entries[key] = PluginEntry(manifest=manifest, instance=instance)
        self._enabled[manifest.id] = enabled
        self._breakers[manifest.id] = CircuitBreaker()

    def get(self, kind: str, plugin_id: str) -> PluginEntry | None:
        return self._entries.get((kind, plugin_id))

    def require(self, plugin_id: str) -> PluginEntry:
        entry = self._entry_by_id(plugin_id)
        if entry is None:
            raise PortError(code="not_found", message=f"unknown plugin: {plugin_id}")
        return entry

    def _entry_by_id(self, plugin_id: str) -> PluginEntry | None:
        return next((e for (_k, pid), e in self._entries.items() if pid == plugin_id), None)

    def list_by_kind(self, kind: str) -> Sequence[PluginEntry]:
        return [e for (k, _), e in self._entries.items() if k == kind]

    def ordered(self, kind: str) -> Sequence[PluginEntry]:
        """Return plugins of ``kind`` ordered by ``default_order`` then id."""
        entries = self.list_by_kind(kind)
        return sorted(
            entries,
            key=lambda e: (getattr(e.instance, "default_order", 0), e.manifest.id),
        )

    def enabled_ids(self) -> set[str]:
        return {pid for pid, enabled in self._enabled.items() if enabled}

    def is_enabled(self, plugin_id: str) -> bool:
        return self._enabled.get(plugin_id, True)

    def set_enabled(self, plugin_id: str, enabled: bool) -> None:
        self.require(plugin_id)
        self._enabled[plugin_id] = enabled

    def reset_circuit(self, plugin_id: str) -> None:
        self.require(plugin_id)
        self._breakers[plugin_id] = CircuitBreaker()

    def breaker(self, plugin_id: str) -> CircuitBreaker:
        self.require(plugin_id)
        return self._breakers[plugin_id]

    def shim_for(self, plugin_id: str, *, timeout_s: float | None = None) -> IsolationShim:
        breaker = self.breaker(plugin_id)

        def on_change(health: dict[str, Any]) -> None:
            self._schedule_health_persist(plugin_id, health)

        return IsolationShim(
            breaker,
            timeout_s=timeout_s if timeout_s is not None else self._default_timeout_s,
            logger=self._logger,
            on_change=on_change,
        )

    async def invoke(
        self, plugin_id: str, fn: Callable[..., Awaitable[Any]], *args: Any
    ) -> Any:
        """Call a plugin through its isolation shim.

        The shim applies the timeout, captures exceptions and feeds the
        circuit breaker, so a hanging or failing plugin is contained.
        """
        shim = self.shim_for(plugin_id)
        return await shim.call(fn, *args)

    def capabilities(self, plugin_id: str | None = None) -> dict[str, frozenset[str]]:
        if plugin_id is not None:
            entry = self.require(plugin_id)
            return {plugin_id: entry.manifest.capabilities}
        return {pid: e.manifest.capabilities for (_k, pid), e in self._entries.items()}

    def health(self, plugin_id: str) -> dict[str, Any]:
        return self.breaker(plugin_id).to_health()

    def all_health(self) -> dict[str, dict[str, Any]]:
        return {pid: b.to_health() for pid, b in self._breakers.items()}

    def describe(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for (_kind, pid), entry in self._entries.items():
            manifest = entry.manifest
            items.append(
                {
                    "plugin_id": pid,
                    "kind": manifest.kind,
                    "version": manifest.version,
                    "api_version": manifest.api_version,
                    "enabled": self.is_enabled(pid),
                    "health": self._breakers[pid].to_health(),
                    "capabilities": sorted(manifest.capabilities),
                    "network_hosts": list(manifest.network_hosts),
                    "description": manifest.description,
                }
            )
        return sorted(items, key=lambda item: (str(item["kind"]), str(item["plugin_id"])))

    async def persist_state(self) -> None:
        if self._state_repo is None:
            return
        for (kind, pid), entry in self._entries.items():
            await self._state_repo.upsert_plugin(
                pid,
                kind,
                entry.manifest.version,
                entry.manifest.api_version,
                enabled=self._enabled.get(pid, True),
                health=self.breaker(pid).to_health(),
            )

    async def load_state(self) -> None:
        if self._state_repo is None:
            return
        rows = await self._state_repo.list_plugins()
        for row in rows:
            pid = str(row["plugin_id"])
            if pid in self._enabled:
                self._enabled[pid] = bool(row.get("enabled", True))
            health = row.get("health") or {}
            if pid in self._breakers and health:
                self._breakers[pid] = CircuitBreaker.from_health(cast(dict[str, Any], health))

    def _schedule_health_persist(self, plugin_id: str, health: dict[str, Any]) -> None:
        if self._state_repo is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._persist_health(plugin_id, health))

    async def _persist_health(self, plugin_id: str, health: dict[str, Any]) -> None:
        if self._state_repo is None:
            return
        try:
            await self._state_repo.set_health(plugin_id, health)
        except Exception:  # noqa: BLE001 - health persistence must not break plugin calls
            if self._logger is not None:
                self._logger.warning("plugin.health_persist_failed", plugin_id=plugin_id)


def make_registry(
    state_repo: PluginStateRepository | None = None,
    *,
    logger: StructuredLogger | None = None,
    default_timeout_s: float = 30.0,
) -> PluginRegistry:
    return PluginRegistry(
        state_repo=state_repo, logger=logger, default_timeout_s=default_timeout_s
    )


__all__ = [
    "PluginDeps",
    "PluginEntry",
    "PluginRegistry",
    "PluginRegistryError",
    "make_registry",
]
