"""Resolve a merged company to its survivor from the merge audit.

The merge log is append-only, so the current identity view is a pure function of
the newest decision per merged company. Chains are followed with a cycle guard:
a corrupt or hand-edited chain stops resolving instead of looping.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence


def latest_decisions(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, Mapping[str, object]]:
    """Newest row per merged company; later rows in the sequence win ties."""
    latest: dict[str, Mapping[str, object]] = {}
    for row in rows:
        merged_id = row.get("merged_id")
        if isinstance(merged_id, str) and merged_id:
            latest[merged_id] = row
    return latest


def survivor_of(
    company_id: str, latest: Mapping[str, Mapping[str, object]]
) -> str:
    seen: set[str] = set()
    current = company_id
    while current not in seen:
        row = latest.get(current)
        if row is None or str(row.get("action")) != "merge":
            return current
        seen.add(current)
        next_id = row.get("survivor_id")
        if not isinstance(next_id, str) or not next_id:
            return current
        current = next_id
    return current


def canonical_map(rows: Sequence[Mapping[str, object]]) -> dict[str, str]:
    """Every merged id that currently resolves elsewhere, and where it goes."""
    latest = latest_decisions(rows)
    out: dict[str, str] = {}
    for merged_id in latest:
        survivor = survivor_of(merged_id, latest)
        if survivor != merged_id:
            out[merged_id] = survivor
    return out


__all__ = ["canonical_map", "latest_decisions", "survivor_of"]
