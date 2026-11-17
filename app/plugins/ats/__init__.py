"""ATS connectors (M1+).

Each connector is registered as a ``connector`` plugin, so it is listed in
``plugins.list`` with its health and is invoked through the isolation shim
(timeout + circuit breaker) instead of being called directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.plugins.ats.ashby import AshbyConnector
from app.plugins.ats.career_site import CareerSiteConnector
from app.plugins.ats.greenhouse import GreenhouseConnector
from app.plugins.ats.icims import IcmsConnector
from app.plugins.ats.lever import LeverConnector
from app.plugins.ats.smartrecruiters import SmartRecruitersConnector
from app.plugins.ats.workday import WorkdayConnector

if TYPE_CHECKING:
    from app.plugins.registry import PluginDeps, PluginRegistry

#: Every connector the worker knows about.
CONNECTOR_CLASSES = (
    GreenhouseConnector,
    AshbyConnector,
    LeverConnector,
    SmartRecruitersConnector,
    WorkdayConnector,
    IcmsConnector,
    CareerSiteConnector,
)

DESCRIPTIONS = {
    "ats.greenhouse": "Greenhouse job boards (boards.greenhouse.io).",
    "ats.ashby": "Ashby job boards (jobs.ashbyhq.com).",
    "ats.lever": "Lever job boards (jobs.lever.co).",
    "ats.smartrecruiters": "SmartRecruiters job boards (api.smartrecruiters.com).",
    "ats.workday": "Workday job boards (myworkdayjobs.com).",
    "ats.icims": "iCIMS career sites (icims.com).",
    "ats.career_site": "Generic career page: fetches the page for the extraction pipeline.",
}


def register(registry: PluginRegistry, deps: PluginDeps) -> None:
    """Register every builtin ATS connector."""
    from app.domain.ports.plugins import PluginManifest

    for connector_class in CONNECTOR_CLASSES:
        connector = connector_class()
        registry.add(
            PluginManifest(
                id=connector.id,
                kind="connector",
                version=connector.version,
                api_version="1",
                capabilities=frozenset({"list_jobs", "fetch_job"}),
                network_hosts=tuple(connector.capabilities.network_hosts),
                description=DESCRIPTIONS.get(connector.id, ""),
            ),
            connector,
        )
