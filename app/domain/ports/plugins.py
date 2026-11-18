from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
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

    def list_by_kind(self, kind: str) -> Sequence[Any]: ...

    def is_enabled(self, plugin_id: str) -> bool: ...

    async def invoke(
        self, plugin_id: str, fn: Callable[..., Awaitable[Any]], *args: Any,
        timeout_s: float | None = None,
    ) -> Any: ...

    def require(self, plugin_id: str) -> Any: ...

    def set_enabled(self, plugin_id: str, enabled: bool) -> None: ...

    def reset_circuit(self, plugin_id: str) -> None: ...

    def capabilities(self, plugin_id: str | None = None) -> dict[str, frozenset[str]]: ...


class PluginInvoker(Protocol):
    """Calls a plugin through its isolation shim.

    The shim applies the timeout, captures exceptions and feeds the circuit
    breaker, so a hanging or failing plugin cannot take the worker down.
    """

    async def invoke(
        self, plugin_id: str, fn: Callable[..., Awaitable[Any]], *args: Any
    ) -> Any: ...

