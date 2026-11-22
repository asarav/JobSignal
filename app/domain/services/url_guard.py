"""Which URLs the crawler refuses to fetch (spec §16, §19.5.8).

A crawler that follows whatever URL a page or a discovered source names is an
SSRF primitive: the desktop's own loopback services, the local network and a
cloud metadata endpoint are all one link away. This module is the single answer
to "may we send a request to this URL?" and returns a short reason when the
answer is no.

The check is pure and cheap (no DNS, no sockets), so it can run on every hop of
a redirect chain, not only on the first request. It blocks what a hostile page
can actually put in a link: non-http(s) schemes, local hostnames, the numeric
aliases of loopback, and private, loopback, link-local, reserved or unspecified
address literals.

A hostname that *resolves* to a private address is not resolved here; the spec
asks for the address ranges and schemes, and doing DNS inside a pure domain
service would make crawl decisions depend on the network. The M11 security
review covers resolver-level rebinding.
"""

from __future__ import annotations

from ipaddress import ip_address
from urllib.parse import urlparse

ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Names that only ever mean "this machine" (or a cloud metadata service).
LOCAL_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "metadata.google.internal",
    }
)
LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa")


def unsafe_url_reason(url: str) -> str | None:
    """Why ``url`` must not be fetched, or ``None`` when it is acceptable."""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        return f"scheme {scheme or '(none)'} is not http or https"
    return unsafe_host_reason(parsed.hostname)


def unsafe_host_reason(host: str | None) -> str | None:
    """Why ``host`` must not be fetched, or ``None`` when it is acceptable."""
    if not host:
        return "URL has no host"
    name = host.strip().lower().rstrip(".")
    if not name:
        return "URL has no host"
    if name in LOCAL_HOSTNAMES or name.endswith(LOCAL_SUFFIXES):
        return f"{host} is a local hostname"
    if _is_numeric_host(name):
        # ``http://2130706433/`` and ``http://0x7f000001/`` are 127.0.0.1.
        return f"{host} is an address literal, not a public hostname"
    try:
        address = ip_address(name)
    except ValueError:
        return None
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        return unsafe_host_reason(str(mapped))
    if not address.is_global:
        return f"{host} is not a public address"
    return None


def _is_numeric_host(name: str) -> bool:
    if name.isdigit():
        return True
    return name.startswith("0x") and all(char in "0123456789abcdefx" for char in name)
