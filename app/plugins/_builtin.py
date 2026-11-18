"""Builtin plugin registration.

Adding a builtin plugin = new package under ``app/plugins/<kind>/<name>/`` with
a ``register.py`` exposing ``PLUGIN`` and ``register(registry, deps)`` plus one
entry in :data:`BUILTIN_PLUGINS`.  No edits to application services.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.plugins.registry import PluginDeps, PluginRegistry

# (module path, register function name) pairs.  Keep this list flat.
BUILTIN_PLUGINS: list[tuple[str, str]] = [
    ("app.plugins.extraction", "register"),
    ("app.plugins.ats", "register"),
    ("app.plugins.discovery", "register"),
]


def register_builtin_plugins(registry: PluginRegistry, deps: PluginDeps) -> None:
    """Register every builtin plugin listed in :data:`BUILTIN_PLUGINS`."""
    for module_path, register_name in BUILTIN_PLUGINS:
        module = __import__(module_path, fromlist=[register_name])
        register = getattr(module, register_name)
        register(registry, deps)