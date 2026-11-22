"""Discovery providers (M4).

Two kinds of provider feed the daily pass:

* ``discovery.web_index`` — enumerates ATS tenants from a public web URL
  index, so discovery does not depend on a bundled list or on the user adding
  sources.  Each run walks a slice of the tenant space.
* ``discovery.ats_directory`` — a bundled seed list (25,716 boards, each
  confirmed live through its public ATS API and ordered largest-first) used as
  a warm start for cold installs and as a test fixture.  Regenerate via
  ``scripts/build_seed.py``.  It is not the discovery engine.

Optional providers (search engines) stay behind their feature flag and are
registered by their own module when enabled.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.plugins.discovery.ats_directory import AtsDirectoryProvider
from app.plugins.discovery.domain_index import DomainIndexProvider
from app.plugins.discovery.funnel import FunnelProvider
from app.plugins.discovery.oracle_recruiting import OracleRecruitingProvider
from app.plugins.discovery.web_index import CDX_LIMIT, WebIndexProvider

if TYPE_CHECKING:
    from app.plugins.registry import PluginDeps, PluginRegistry

#: Bundled bootstrap seed list of public ATS tenants, shipped as package data.
SEEDS_PATH = Path(__file__).resolve().parent / "seeds" / "ats_tenants.json"


def _discovery_settings(deps: PluginDeps) -> dict[str, Any]:
    """Discovery settings as a mapping (validated model or legacy dict).

    Plugins may not import ``app.application``, so this reads the config
    duck-typed instead of calling the application helper.
    """
    settings = getattr(getattr(deps, "config", None), "discovery", None)
    if isinstance(settings, Mapping):
        return dict(settings)
    dump = getattr(settings, "model_dump", None)
    if callable(dump):
        data = dump()
        if isinstance(data, Mapping):
            return dict(data)
    return {}


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
            version="1.2.0",
            api_version="1",
            capabilities=frozenset({"discover"}),
            network_hosts=("index.commoncrawl.org", "web.archive.org"),
            description=(
                "Enumerates ATS tenants from a public web URL index; walks a "
                "slice per run so coverage grows without seeds (M4)."
            ),
        ),
        WebIndexProvider(
            limit=_positive_int(settings.get("web_index_limit"), 200),
            cdx_limit=_positive_int(settings.get("web_index_cdx_limit"), CDX_LIMIT),
        ),
    )

    registry.add(
        PluginManifest(
            id="discovery.funnel",
            kind="discovery",
            version="1.1.0",
            api_version="1",
            capabilities=frozenset({"discover", "harvest_postings"}),
            network_hosts=("index.commoncrawl.org", "web.archive.org"),
            description=(
                "Harvests posting URLs from the same public web-index slices "
                "used for board discovery (M6)."
            ),
        ),
        FunnelProvider(
            limit=_positive_int(settings.get("web_index_limit"), 200),
            cdx_limit=_positive_int(settings.get("web_index_cdx_limit"), CDX_LIMIT),
        ),
    )

    registry.add(
        PluginManifest(
            id="discovery.domain_index",
            kind="discovery",
            version="1.0.0",
            api_version="1",
            capabilities=frozenset({"discover"}),
            network_hosts=("web.archive.org",),
            description=(
                "Enumerates subdomain and path-addressed ATS tenants (Workday, "
                "iCIMS, BambooHR, Eightfold, UKG, Workable) from a public web index."
            ),
        ),
        DomainIndexProvider(),
    )

    registry.add(
        PluginManifest(
            id="discovery.oracle_recruiting",
            kind="discovery",
            version="1.0.0",
            api_version="1",
            capabilities=frozenset({"discover"}),
            network_hosts=("web.archive.org",),
            description=(
                "Enumerates Oracle Recruiting Cloud career sites by rotating "
                "Oracle HCM pods in a public URL index."
            ),
        ),
        OracleRecruitingProvider(),
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
