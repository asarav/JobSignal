"""Tier assignment for discovered sources (M6).

Tiers are recomputed on every funnel pass from the candidate profile's active
resume, its manual skill additions and the hard filters of the user's search
profiles.  The result is stored on the source schedule (``tier`` and
``tier_reason``) so the crawler and the Discover screen read the same value.
Only a *change of tier* is evented; the reason is refreshed with the stored
value so the UI always explains the current tier.

Without a candidate profile (or without any skills, titles or filters) tiering
is skipped and discovery keeps its plain due-based cadence; a tier stored by an
earlier pass is removed with it, so a cleared profile cannot keep a board on a
narrowed interval forever.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from app.application.container import Container
from app.domain.models.source import SourceConfig
from app.domain.services.keywords import normalize_skill_ids
from app.domain.services.tiers import TIER_COLD, assign_tier

DEFAULT_PROFILE_ID = "default"

_TITLE_KEYS = ("titles", "target_titles", "roles")


class TierService:
    def __init__(self, container: Container) -> None:
        self._container = container

    async def assign(self, sources: Sequence[SourceConfig]) -> list[dict[str, Any]]:
        """Recompute and persist tiers; returns the sources whose tier changed."""
        if not sources:
            return []
        async with self._container.uow_factory() as uow:
            skills, titles, filter_sets = await _signals(uow)
            if not skills and not titles and not filter_sets:
                # Nothing relevant to rank against any more (no profile yet, or
                # the profile/skills/filters were cleared).  A tier assigned
                # earlier is no longer justified by anything, so it must not
                # outlive its inputs and keep narrowing the crawl interval.
                cleared = await _clear_tiers(uow, sources)
                await uow.commit()
                self._notify(cleared)
                if cleared:
                    self._container.logger.info(
                        "discovery.tier_cleared", sources=len(cleared)
                    )
                return cleared

            # One query for every source instead of one per source: a pass can
            # see hundreds of boards and this runs inside a single transaction.
            jobs_by_source = await uow.jobs.list_for_sources(
                [source.id for source in sources], limit=50
            )

            changed: list[dict[str, Any]] = []
            for source in sources:
                decision = assign_tier(
                    jobs_by_source.get(source.id, ()),
                    skills=skills,
                    titles=titles,
                    filter_sets=filter_sets,
                )
                previous = source.schedule.get("tier")
                previous_reason = source.schedule.get("tier_reason")
                if previous == decision.tier and previous_reason == decision.reason:
                    continue
                schedule = dict(source.schedule)
                schedule["tier"] = decision.tier
                schedule["tier_reason"] = decision.reason
                await uow.sources.save(replace(source, schedule=schedule))
                if previous != decision.tier:
                    changed.append(
                        {
                            "source_id": source.id,
                            "url": source.url,
                            "from": previous if isinstance(previous, str) else None,
                            "to": decision.tier,
                            "reason": decision.reason,
                        }
                    )
            await uow.commit()

        self._notify(changed)
        return changed

    def _notify(self, changed: Sequence[Mapping[str, Any]]) -> None:
        """Publish real tier transitions.

        The first assignment of a source (``from`` is ``None``) is a baseline,
        not a transition: a cold-start pass would otherwise publish hundreds
        of events at once for boards whose cadence simply became known.
        """
        for change in changed:
            if change.get("from") is None:
                continue
            self._container.events.publish("discovery.tier_changed", dict(change))
            self._container.logger.info("discovery.tier_changed", **change)


async def _signals(
    uow: Any,
) -> tuple[list[str], list[str], list[Mapping[str, object]]]:
    """The candidate signals a tier is ranked against (M5 profile + M3 filters)."""
    profile = await uow.resumes.get_profile(DEFAULT_PROFILE_ID)
    skills: list[str] = []
    titles: list[str] = []
    filter_sets: list[Mapping[str, object]] = []
    if profile is None:
        return skills, titles, filter_sets

    version_id = str(profile.get("active_resume_version_id") or "")
    if version_id:
        skills = [
            str(row["keyword_id"]) for row in await uow.resumes.get_skills(version_id)
        ]
    overrides = await uow.resumes.get_overrides(DEFAULT_PROFILE_ID)
    for key in normalize_skill_ids(overrides.get("skills_added")):
        if key not in skills:
            skills.append(key)

    titles = _titles(overrides.get("preferences"))
    titles.extend(_titles(overrides))
    for search_profile in await uow.search_profiles.list():
        if search_profile.hard_filters:
            filter_sets.append(search_profile.hard_filters)
        titles.extend(_titles(search_profile.preferences))
    return skills, titles, filter_sets


async def _clear_tiers(
    uow: Any, sources: Sequence[SourceConfig]
) -> list[dict[str, Any]]:
    """Remove stored tiers and report them as transitions back to none."""
    cleared: list[dict[str, Any]] = []
    reason = (
        "tiering paused: no candidate profile, skills, target titles or "
        "hard filters are set any more"
    )
    for source in sources:
        if "tier" not in source.schedule and "tier_reason" not in source.schedule:
            continue
        previous = source.schedule.get("tier")
        schedule = dict(source.schedule)
        schedule.pop("tier", None)
        schedule.pop("tier_reason", None)
        await uow.sources.save(replace(source, schedule=schedule))
        cleared.append(
            {
                "source_id": source.id,
                "url": source.url,
                "from": previous if isinstance(previous, str) else None,
                "to": None,
                "reason": reason,
            }
        )
    return cleared


def _titles(value: object) -> list[str]:
    if not isinstance(value, Mapping):
        return []
    titles: list[str] = []
    for key in _TITLE_KEYS:
        raw = value.get(key)
        if isinstance(raw, str):
            titles.append(raw)
        elif isinstance(raw, (list, tuple)):
            titles.extend(str(item) for item in raw if str(item).strip())
    return titles


#: Kept for callers that only need the cold fallback.
COLD_TIER = TIER_COLD
