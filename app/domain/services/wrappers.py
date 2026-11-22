"""Wrapper career-site detection (M4+).

Marketing-site vendors (Beamery, Radancy, Clinch, SmashFly, Phenom, ...) often
wrap an employer's real ATS in an iframe or point their apply links at it.
Rather than writing a scraper per vendor, scan the rendered HTML for the
embedded ATS host and route to the matching connector:

- Workday, iCIMS, Oracle Recruiting Cloud, Taleo, Eightfold, Phenom,
  Greenhouse, Lever, Ashby, SmartRecruiters, Workable and BambooHR are
  recognised;
- Phenom pages are recognised by their widget markers and stay on the same
  employer host, because the widget endpoint is tenant-specific.

When no ATS is embedded, the caller falls back to harvesting job links from
the page and its sitemap (the generic career-site connector).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlsplit

#: URLs in attributes and inline script configuration. Escaped slashes and
#: HTML entities are normalised before matching.
_URL_RE = re.compile(r"https?://[^\s\"'<>\\]+")
_ASSET_SUFFIXES = (
    ".css",
    ".js",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".webp",
    ".ico",
    ".pdf",
    ".woff",
    ".woff2",
)
_PHENOM_MARKERS = ("ddokey", "refinesearch", "phenompeople")

#: (host suffix, path marker, connector id). Empty path marker means the host
#: alone is enough.
_URL_PATTERNS: tuple[tuple[str, str, str], ...] = (
    ("myworkdayjobs.com", "", "ats.workday"),
    ("myworkdaysite.com", "", "ats.workday"),
    ("oraclecloud.com", "/hcmui/candidateexperience", "ats.oracle_recruiting"),
    ("icims.com", "", "ats.icims"),
    ("taleo.net", "/careersection/", "ats.taleo"),
    ("eightfold.ai", "", "ats.eightfold"),
    ("apply.workable.com", "", "ats.workable"),
    ("bamboohr.com", "", "ats.bamboohr"),
    ("smartrecruiters.com", "", "ats.smartrecruiters"),
    ("boards.greenhouse.io", "", "ats.greenhouse"),
    ("jobs.lever.co", "", "ats.lever"),
    ("jobs.ashbyhq.com", "", "ats.ashby"),
)


@dataclass(frozen=True, slots=True)
class EmbeddedAts:
    """An ATS discovered in a wrapper page."""

    connector_id: str
    url: str
    marker: str
    tenant_key: str | None = None


def detect_embedded_ats(html: str, base_url: str) -> EmbeddedAts | None:
    """Return the ATS embedded in ``html``, or ``None`` when it is generic."""
    normalised = unescape(html).replace("\\/", "/")
    lowered = normalised.lower()
    # Eightfold's custom-host pages carry their API origin in this marker; the
    # page URL itself is the reliable board URL (the marker's value can point
    # at a legacy host that is not where the board lives).
    if "_ef_group_id" in lowered:
        return EmbeddedAts("ats.eightfold", base_url, "eightfold")
    for raw in _URL_RE.findall(normalised):
        url = raw.rstrip(").,;\"'}")
        parts = urlsplit(url)
        host = parts.netloc.lower()
        path = parts.path.lower()
        if not host or host == urlsplit(base_url).netloc.lower():
            continue
        if path.endswith(_ASSET_SUFFIXES) or "/assets/" in path:
            continue
        for suffix, path_marker, connector_id in _URL_PATTERNS:
            if not (host == suffix or host.endswith(f".{suffix}")):
                continue
            if path_marker and path_marker not in path:
                continue
            if connector_id == "ats.eightfold":
                if "/api/apply/v2/jobs" in path:
                    return EmbeddedAts(
                        connector_id,
                        f"{parts.scheme}://{parts.netloc}/careers",
                        suffix,
                    )
                if "/careers" not in path and "/jobs" not in path:
                    continue
            if connector_id == "ats.workday":
                board = _workday_board(url)
                if board is not None:
                    return EmbeddedAts(connector_id, board[0], suffix, board[1])
                continue
            return EmbeddedAts(connector_id, url, suffix)

    if any(marker in lowered for marker in _PHENOM_MARKERS):
        return EmbeddedAts("ats.phenom", base_url, "phenom")
    if "eightfold" in lowered and (
        "/api/apply/v2/jobs" in lowered or "static.vscdn.net" in lowered
    ):
        return EmbeddedAts("ats.eightfold", base_url, "eightfold")
    return None


def _workday_board(url: str) -> tuple[str, str] | None:
    """Turn a Workday apply/job URL into its board URL and tenant label."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    labels = host.split(".")
    if len(labels) < 3 or not (
        host.endswith(".myworkdayjobs.com") or host.endswith(".myworkdaysite.com")
    ):
        return None
    tenant = labels[0]
    segments = [segment for segment in parts.path.split("/") if segment]
    if not segments:
        return None
    # /en-US/Site/job/... and /Site/job/... both have the site first (after an
    # optional locale). /wday/cxs/... is an API URL, not a board.
    if segments[0].lower() == "wday":
        return None
    if re.match(r"^[a-z]{2}(?:-[A-Za-z]{2,4})?$", segments[0]):
        segments = segments[1:]
    if not segments:
        return None
    site = segments[0]
    if site.lower() in {"job", "jobs", "apply", "login", "assets"}:
        return None
    return f"https://{host}/{site}", tenant
