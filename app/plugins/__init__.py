"""Internal plugin system (M0: registry + isolation shim + circuit breaker).

Plugins live in ``app/plugins/<kind>/<name>/`` and expose a ``register.py`` with::

    PLUGIN = PluginManifest(id=..., kind=..., version=..., api_version="1", ...)

    def register(registry: "PluginRegistry", deps: PluginDeps) -> None: ...

Adding a plugin requires only the new package and one line in
:func:`app.plugins._builtin.register_builtin_plugins`.
"""

from __future__ import annotations

from app.plugins.isolation import CircuitBreaker, CircuitState, IsolationShim
from app.plugins.registry import (
    PluginDeps,
    PluginEntry,
    PluginRegistry,
    PluginRegistryError,
)

__all__ = [
    "CircuitBreaker",
    "CircuitState",
    "IsolationShim",
    "PluginDeps",
    "PluginEntry",
    "PluginRegistry",
    "PluginRegistryError",
]