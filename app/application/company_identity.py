"""Company merge and unmerge over an append-only decision log (M9).

The companies table is never rewritten. A merge appends a decision; reads resolve
a merged id to its survivor through `canonical_map`. Unmerge appends the opposite
decision, so the earlier rows stay auditable and the prior view returns exactly.
"""

from __future__ import annotations

from typing import Any

from app.domain.models.common import PortError
from app.domain.models.company_merge import CompanyMergeRecord
from app.domain.services.company_resolution import canonical_map, survivor_of


class CompanyIdentityService:
    def __init__(self, container: Any) -> None:
        self._container = container

    async def canonical_map(self, uow: Any) -> dict[str, str]:
        rows = await uow.company_merges.latest_actions()
        return canonical_map(rows)

    async def resolve(self, uow: Any, company_id: str) -> str:
        rows = await uow.company_merges.latest_actions()
        return survivor_of(company_id, {str(row["merged_id"]): row for row in rows})

    async def merge(
        self, company_id: str, into_id: str, reason: str = ""
    ) -> dict[str, Any]:
        if company_id == into_id:
            raise PortError(
                code="validation.invalid_params",
                message="a company cannot be merged into itself",
                retryable=False,
            )
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            company = await uow.companies.get(company_id)
            target = await uow.companies.get(into_id)
            if company is None or target is None:
                missing = company_id if company is None else into_id
                raise PortError(
                    code="not_found",
                    message=f"unknown company: {missing}",
                    retryable=False,
                )
            rows = await uow.company_merges.latest_actions()
            latest = {str(row["merged_id"]): row for row in rows}
            survivor = survivor_of(into_id, latest)
            if survivor == company_id:
                raise PortError(
                    code="validation.merge_cycle",
                    message=(
                        "that merge would create a cycle: the target already "
                        "resolves to this company"
                    ),
                    retryable=False,
                )
            if survivor_of(company_id, latest) == survivor:
                await uow.commit()
                return {
                    "company_id": company_id,
                    "survivor_id": survivor,
                    "action": "merge",
                    "changed": False,
                }
            record = CompanyMergeRecord(
                id=f"cm_{self._container.ids.new_id()[:16]}",
                survivor_id=survivor,
                merged_id=company_id,
                action="merge",
                created_at=now,
                reason=reason,
                created_by="user",
            )
            await uow.company_merges.add(record)
            await uow.commit()
        return {
            "company_id": company_id,
            "survivor_id": survivor,
            "action": "merge",
            "changed": True,
            "record_id": record.id,
        }

    async def unmerge(self, company_id: str, reason: str = "") -> dict[str, Any]:
        now = self._container.clock.now()
        async with self._container.uow_factory() as uow:
            company = await uow.companies.get(company_id)
            if company is None:
                raise PortError(
                    code="not_found",
                    message=f"unknown company: {company_id}",
                    retryable=False,
                )
            rows = await uow.company_merges.latest_actions()
            latest = {str(row["merged_id"]): row for row in rows}
            current = survivor_of(company_id, latest)
            if current == company_id:
                await uow.commit()
                return {
                    "company_id": company_id,
                    "survivor_id": company_id,
                    "action": "unmerge",
                    "changed": False,
                }
            record = CompanyMergeRecord(
                id=f"cm_{self._container.ids.new_id()[:16]}",
                survivor_id=current,
                merged_id=company_id,
                action="unmerge",
                created_at=now,
                reason=reason,
                created_by="user",
            )
            await uow.company_merges.add(record)
            await uow.commit()
        return {
            "company_id": company_id,
            "survivor_id": company_id,
            "previous_survivor_id": current,
            "action": "unmerge",
            "changed": True,
            "record_id": record.id,
        }

    async def history(
        self, company_id: str | None = None, *, limit: int = 200
    ) -> dict[str, Any]:
        async with self._container.uow_factory() as uow:
            rows = (
                await uow.company_merges.list_for_company(company_id, limit=limit)
                if company_id
                else await uow.company_merges.list_all(limit=limit)
            )
            merged = await self.canonical_map(uow)
            names = {
                str(row.get("id")): str(row.get("canonical_name") or row.get("id"))
                for row in await uow.companies.list()
            }
            await uow.commit()
        return {
            "company_id": company_id,
            "records": [
                {
                    **dict(row),
                    "merged_name": names.get(str(row.get("merged_id"))),
                    "survivor_name": names.get(str(row.get("survivor_id"))),
                }
                for row in rows
            ],
            "currently_merged": merged,
            "total": len(rows),
        }


__all__ = ["CompanyIdentityService"]
