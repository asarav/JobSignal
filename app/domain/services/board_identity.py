"""Stable identity of a job board, independent of how its URL is written.

The same employer board is reachable through several URL spellings:

* Workday: ``{tenant}.wd1.myworkdayjobs.com/en-US/Careers`` and the ``fr-CA``
  or locale-less form of the same career site; a tenant can also run several
  sites, which are *different* boards;
* Oracle Recruiting Cloud: one host can publish several ``sites/{code}``
  boards, and the locale in front of the site is a mirror;
* Greenhouse: ``boards.greenhouse.io`` and ``job-boards.greenhouse.io`` are the
  same tenant;
* Paylocity: the readable slug after the company id is decorative, and one
  employer is indexed under several slugs;
* Taleo: a tenant can publish several career sections.

Registering, deduplicating and merging on the raw URL therefore creates
duplicates and re-adds boards the user removed.  This module is the one place
that answers "which board is this?", and both the runtime and the seed builder
use it.

Pure domain logic: standard library only.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

#: Subdomain-addressed ATSs: the employer is the first label of the host.
_SUBDOMAIN_SUFFIXES: tuple[str, ...] = (
    ".myworkdayjobs.com",
    ".myworkdaysite.com",
    ".icims.com",
    ".bamboohr.com",
    ".eightfold.ai",
    ".applytojob.com",
    ".taleo.net",
)

_LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2,4})?$")
_WORKDAY_HOST_RE = re.compile(
    r"^(?P<tenant>[a-z0-9][a-z0-9-]*)\.(?:impl-)?wd\d+\.myworkdayjobs\.com$"
)
_WORKDAY_PLAIN_RE = re.compile(r"^(?P<tenant>[a-z0-9][a-z0-9-]*)\.myworkdayjobs\.com$")
_ORACLE_PATH_RE = re.compile(
    r"^/hcmUI/CandidateExperience(?:/(?P<lang>[a-z]{2}(?:-[A-Za-z]{2,4})?))?"
    r"/sites/(?P<site>[A-Za-z0-9_]+)(?:/|$)",
    re.IGNORECASE,
)
_TALEO_PATH_RE = re.compile(r"^/careersection/(?P<section>[A-Za-z0-9_]+)(?:/|$)", re.IGNORECASE)
_PAYLOCITY_RE = re.compile(
    r"^/recruiting/jobs/all/(?P<company>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12})(?:/|$)",
    re.IGNORECASE,
)
_ULTIPRO_RE = re.compile(
    r"^/(?P<tenant>[A-Za-z0-9]{2,32})/JobBoard/"
    r"(?P<board>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:/|$)"
)
#: First path segments that are site chrome, never an employer.
_RESERVED_SEGMENTS = frozenset(
    {
        "api",
        "assets",
        "embed",
        "images",
        "job",
        "jobs",
        "robots",
        "search",
        "sitemap",
        "static",
        "v1",
        "favicon.ico",
    }
)
_RESERVED_HOSTS = frozenset(
    {"www", "www1", "www2", "assets", "api", "app", "cdn", "static", "login", "secure"}
)


def board_identity(
    connector_id: str | None,
    url: str,
    tenant_key: str | None = None,
) -> tuple[str, ...]:
    """The board's identity as a hashable tuple, or ``()`` for a non-board URL.

    The first element is the connector, so two different ATSs never collide.
    The remaining elements are the connector's own identity: Workday's tenant
    and career site, Oracle's host and site, Taleo's tenant and section, and the
    ATS tenant everywhere else.
    """
    connector = connector_id or ""
    host, path = _split(url)
    if not host:
        return ()
    if connector == "ats.workday":
        key = (tenant_key or "").strip()
        if "|" in key:
            tenant, site = key.split("|", 1)
        else:
            tenant, site = key or _workday_tenant(host), _workday_site(path)
        if not tenant or not site:
            return ()
        return (connector, tenant.lower(), site.lower())
    if connector == "ats.oracle_recruiting":
        key = (tenant_key or "").strip()
        if "|" in key:
            return (connector, key.lower())
        match = _ORACLE_PATH_RE.match(path)
        return (connector, f"{host}|{match.group('site').lower()}" if match else host)
    if connector == "ats.paylocity":
        match = _PAYLOCITY_RE.match(path.lower())
        company = (tenant_key or "").strip().lower() or (
            match.group("company") if match else ""
        )
        return (connector, company) if company else ()
    if connector == "ats.ultipro":
        match = _ULTIPRO_RE.match(path)
        board = (tenant_key or "").strip().lower() or (
            match.group("board").lower() if match else ""
        )
        return (connector, board) if board else ()
    if connector == "ats.taleo":
        key = (tenant_key or "").strip()
        if "|" in key:
            return (connector, key.lower())
        match = _TALEO_PATH_RE.match(path)
        tenant = _subdomain(host, ".taleo.net")
        if not tenant:
            return ()
        section = match.group("section") if match else ""
        return (connector, f"{tenant}|{section}".lower())
    tenant = (tenant_key or "").strip()
    if tenant:
        return (connector, tenant.lower())
    tenant = _first_segment(path) or _subdomain_tenant(host)
    return (connector, tenant) if tenant else ()


def identity_key(connector_id: str | None, url: str, tenant_key: str | None = None) -> str:
    """A string form of :func:`board_identity`, for caches and state."""
    return "|".join(board_identity(connector_id, url, tenant_key))


def _split(url: str) -> tuple[str, str]:
    parts = urlsplit(url if "://" in url else f"https://{url}")
    return parts.netloc.lower(), parts.path


def _workday_tenant(host: str) -> str:
    match = _WORKDAY_HOST_RE.match(host) or _WORKDAY_PLAIN_RE.match(host)
    return match.group("tenant") if match else ""


def _workday_site(path: str) -> str:
    segments = [part for part in path.split("/") if part]
    if not segments:
        return ""
    if _LOCALE_RE.match(segments[0]):
        segments = segments[1:]
    return segments[0] if segments else ""


def _first_segment(path: str) -> str:
    segments = [part for part in path.split("/") if part]
    if not segments:
        return ""
    first = segments[0].lower()
    return "" if first in _RESERVED_SEGMENTS else first


def _subdomain(host: str, suffix: str) -> str:
    if not host.endswith(suffix):
        return ""
    label = host[: -len(suffix)]
    if not label or "." in label or label in _RESERVED_HOSTS:
        return ""
    return label


def _subdomain_tenant(host: str) -> str:
    for suffix in _SUBDOMAIN_SUFFIXES:
        label = _subdomain(host, suffix)
        if label:
            return label
    return ""
