"""ATS connectors (M1+).

Each connector is registered as a ``connector`` plugin, so it is listed in
``plugins.list`` with its health and is invoked through the isolation shim
(timeout + circuit breaker) instead of being called directly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.plugins.ats.amazon import AmazonConnector
from app.plugins.ats.ashby import AshbyConnector
from app.plugins.ats.bamboohr import BambooHrConnector
from app.plugins.ats.career_site import CareerSiteConnector
from app.plugins.ats.eightfold import EightfoldConnector
from app.plugins.ats.greenhouse import GreenhouseConnector
from app.plugins.ats.icims import IcmsConnector
from app.plugins.ats.lever import LeverConnector
from app.plugins.ats.oracle_recruiting import OracleRecruitingConnector
from app.plugins.ats.phenom import PhenomConnector
from app.plugins.ats.smartrecruiters import SmartRecruitersConnector
from app.plugins.ats.taleo import TaleoConnector
from app.plugins.ats.ultipro import UltiproConnector
from app.plugins.ats.workable import WorkableConnector
from app.plugins.ats.workday import WorkdayConnector

if TYPE_CHECKING:
    from app.plugins.registry import PluginDeps, PluginRegistry

#: Every connector the worker knows about.
CONNECTOR_CLASSES = (
    AmazonConnector,
    GreenhouseConnector,
    AshbyConnector,
    LeverConnector,
    SmartRecruitersConnector,
    WorkdayConnector,
    IcmsConnector,
    UltiproConnector,
    WorkableConnector,
    BambooHrConnector,
    OracleRecruitingConnector,
    EightfoldConnector,
    PhenomConnector,
    TaleoConnector,
    CareerSiteConnector,
)

DESCRIPTIONS = {
    "ats.greenhouse": "Greenhouse job boards (boards.greenhouse.io).",
    "ats.ashby": "Ashby job boards (jobs.ashbyhq.com).",
    "ats.lever": "Lever job boards (jobs.lever.co).",
    "ats.smartrecruiters": "SmartRecruiters job boards (api.smartrecruiters.com).",
    "ats.workday": "Workday job boards (myworkdayjobs.com).",
    "ats.icims": "iCIMS career sites (icims.com).",
    "ats.ultipro": "UKG Pro / UltiPro job boards (recruiting.ultipro.com).",
    "ats.workable": "Workable job boards (apply.workable.com).",
    "ats.bamboohr": "BambooHR careers sites ({company}.bamboohr.com).",
    "ats.oracle_recruiting": "Oracle Recruiting Cloud career sites (oraclecloud.com).",
    "ats.eightfold": "Eightfold AI career sites (eightfold.ai and custom hosts).",
    "ats.phenom": "Phenom widget career sites, configurable per tenant.",
    "ats.amazon": "Amazon Jobs (amazon.jobs public search JSON).",
    "ats.taleo": "Taleo career sections (taleo.net), server-rendered postings.",
    "ats.career_site": (
        "Generic career page: routes embedded ATS wrappers, otherwise harvests job links."
    ),
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
