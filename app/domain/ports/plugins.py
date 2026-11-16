from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

PLUGIN_KINDS = (
    "discovery",
    "connector",
    "extraction",
    "matching",
    "posting_signal",
    "risk_model",
    "resume_parser",
    "keyword",
    "browser",
    "form_filler",
    "scoring_model",
)


@dataclass(frozen=True, slots=True)
class PluginManifest:
    id: str
    kind: str
    version: str
    api_version: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
    network_hosts: Sequence[str] = ()
    config_schema: type[Any] | None = None
    description: str = ""


class PluginRegistryPort(Protocol):
    """Application-facing view of the plugin registry.

    Application services must depend on this port rather than
    ``app.plugins.registry.PluginRegistry`` so the dependency rule holds.
    """

    async def persist_state(self) -> None: ...

    async def load_state(self) -> None: ...

    def describe(self) -> list[dict[str, Any]]: ...

    def require(self, plugin_id: str) -> Any: ...

    def set_enabled(self, plugin_id: str, enabled: bool) -> None: ...

    def reset_circuit(self, plugin_id: str) -> None: ...

    def capabilities(self, plugin_id: str | None = None) -> dict[str, frozenset[str]]: ...

