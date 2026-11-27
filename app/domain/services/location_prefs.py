"""Candidate area rules for ranked job lists.

The fit score lowers a posting outside the candidate's stated locations, but a
score alone still leaves that row in the shortlist.  A candidate who is not open
to relocation should also stop seeing known out-of-area roles by default.
Missing location data is treated as unknown, never as a mismatch.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.]*")

#: Words that describe an area without naming it.  Ignoring them keeps
#: "San Francisco Bay Area" from matching every posting that says "area".
_GENERIC = frozenset(
    {
        "the",
        "and",
        "area",
        "metro",
        "metropolitan",
        "region",
        "city",
        "county",
        "state",
        "united",
        "states",
        "country",
        "remote",
        "hybrid",
        "onsite",
        "on",
        "site",
    }
)


def _as_list(value: object) -> list[object]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str):
        return [part for part in value.split(",")]
    return [value]


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) >= 2 and token not in _GENERIC
    }


def preferred_locations(preferences: Mapping[str, object]) -> list[str]:
    """Normalized, non-empty location preferences from the profile."""
    out: list[str] = []
    for value in _as_list(preferences.get("locations")):
        # A single saved string may still hold a comma-separated list.
        for part in str(value).split(","):
            text = part.strip().lower()
            if text and text not in out:
                out.append(text)
    return out


def _wanted_workplaces(preferences: Mapping[str, object]) -> set[str]:
    return {
        str(value).strip().lower()
        for value in _as_list(preferences.get("workplace_types"))
        if str(value).strip()
    }


def _matches(term: str, location: str) -> bool:
    if term in location or location in term:
        return True
    term_tokens = _tokens(term)
    location_tokens = _tokens(location)
    return bool(term_tokens and location_tokens and term_tokens & location_tokens)


def job_in_area(
    location_text: str | None,
    workplace_type: str | None,
    preferences: Mapping[str, object] | None,
) -> bool | None:
    """Whether a posting is in the candidate's area.

    ``None`` means the question cannot be answered (no preference, no location
    data, or a remote role when remote work is not wanted), and the caller must
    keep the row.  ``False`` is a known conflict: the row may be withheld.
    """
    if not preferences:
        return None
    locations = preferred_locations(preferences)
    if not locations:
        return None
    if preferences.get("relocation") is True:
        return True

    workplace = str(workplace_type or "").strip().lower()
    remote_ok = bool(preferences.get("remote_ok")) or "remote" in _wanted_workplaces(
        preferences
    )
    if workplace == "remote":
        # A remote role is not tied to the posting's listed city.
        return True if remote_ok else None

    text = str(location_text or "").strip().lower()
    if not text or text == "unknown":
        return None
    return any(_matches(term, text) for term in locations)