"""Discovery providers (M4).

Two kinds of provider feed the daily pass:

* ``discovery.web_index`` — enumerates ATS tenants from a public web URL
  index, so discovery does not depend on a bundled list or on the user adding
  sources.  Each run walks one slice of the tenant space.
* ``discovery.ats_directory`` — a small offline seed list used as a bootstrap
  on a cold start (and a demo/test fixture).  It is not the discovery engine.

Optional providers (search engines) stay behind their feature flag and are
registered by their own module when enabled.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.plugins.discovery.ats_directory import AtsDirectoryProvider
from app.plugins.discovery.web_index import WebIndexProvider

if TYPE_CHECKING:
    from app.plugins.registry import PluginDeps, PluginRegistry

#: Bundled bootstrap seed list of public ATS tenants, shipped as package data.
SEEDS_PATH = Path(__file__).resolve().parent / "seeds" / "ats_tenants.json"


def _discovery_settings(deps: PluginDeps) -> dict[str, Any]:
    config = getattr(deps, "config", None)
    settings = getattr(config, "discovery", None)
    return dict(settings) if isinstance(settings, dict) else {}


def _positive_int(value: object, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value if value > 0 else default


def register(registry: PluginRegistry, deps: PluginDeps) -> None:
    from app.domain.ports.plugins import PluginManifest

    settings = _discovery_settings(deps)

    registry.add(
        PluginManifest(
            id="discovery.web_index",
            kind="discovery",
            version="1.1.0",
            api_version="1",
            capabilities=frozenset({"discover"}),
            network_hosts=("index.commoncrawl.org", "web.archive.org"),
            description=(
                "Enumerates ATS tenants from a public web URL index; walks one "
                "slice per run so coverage grows without seeds (M4)."
            ),
        ),
        WebIndexProvider(limit=_positive_int(settings.get("web_index_limit"), 100)),
    )

    registry.add(
        PluginManifest(
            id="discovery.ats_directory",
            kind="discovery",
            version="1.0.0",
            api_version="1",
            capabilities=frozenset({"discover"}),
            network_hosts=(),
            description="Offline bootstrap seed list of public ATS tenants (M4).",
        ),
        AtsDirectoryProvider.from_file(SEEDS_PATH),
    )
