"""Stopgap for upstream ATS schema drift.

Connectors read JSON that the ATS's own frontend uses, so a renamed or
restructured field used to look exactly like an empty board: the crawl would
succeed with zero postings and the not-seen rule would start marking every
stored job from that source as removed. ``listing_items`` refuses to treat a
payload that lacks the expected list as empty (unless the payload explicitly
reports a zero total), so the source fails loudly and its jobs stay untouched.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.domain.models.common import PortError


def listing_items(
    payload: Any,
    key: str,
    *,
    ats: str,
    total_key: str = "total",
    first_page: bool = True,
) -> list[Any]:
    """Return the posting list, or raise ``connector.schema_drift``.

    ``first_page`` is False for later pages, where an empty or absent list is
    the normal end of pagination rather than evidence of a changed schema.
    """
    if not isinstance(payload, Mapping) or not payload:
        if not first_page:
            return []
        raise PortError(
            code="connector.schema_drift",
            message=f"{ats}: listing response was empty or not JSON",
        )
    value = payload.get(key)
    if isinstance(value, list):
        return value
    total = payload.get(total_key)
    if value is None and isinstance(total, (int, float)) and total <= 0:
        # The ATS explicitly reports no postings; an absent list is acceptable.
        return []
    if not first_page:
        return []
    raise PortError(
        code="connector.schema_drift",
        message=f"{ats}: listing response has no '{key}' list (endpoint schema changed?)",
    )
