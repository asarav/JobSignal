"""Polite application-endpoint verifier (spec §9.3).

It performs one GET per job at most once per configured interval, through the
same replaceable HTTP port as crawling, and records what the page said. It never
submits, clicks, fills or follows an application flow. A blocked page or a login
wall is recorded as such and later treated as missing evidence.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import timedelta
from typing import Any

from app.application.container import Container
from app.domain.models.common import Err, PortError
from app.domain.models.http import HttpRequest
from app.domain.models.posting import EndpointObservationRecord
from app.domain.services.endpoint_check import classify_endpoint


class EndpointVerifier:
    def __init__(self, container: Container, http: Any = None) -> None:
        self._container = container
        self._http = http or container.extra.get("policy_http") or container.http

    async def verify(
        self,
        *,
        job_ids: Sequence[str] | None = None,
        limit: int = 25,
        force: bool = False,
    ) -> dict[str, Any]:
        if self._http is None:
            raise PortError(
                code="plugin.unavailable",
                message="no HttpClient adapter is configured",
            )
        now = self._container.clock.now()
        interval_days = self._interval_days()
        targets = (
            list(job_ids)
            if job_ids
            else await self._due_jobs(now, interval_days, limit, force=force)
        )
        states: dict[str, int] = {}
        checked = 0
        for job_id in targets:
            outcome = await self._check_one(job_id, now)
            checked += 1
            states[outcome] = states.get(outcome, 0) + 1
        if checked:
            self._container.events.publish(
                "posting_intelligence.updated",
                {"job_ids": list(targets), "endpoint_states": states},
            )
        return {"checked": checked, "states": states, "job_ids": list(targets)}

    def _interval_days(self) -> int:
        pi = getattr(self._container.config, "posting_intelligence", None)
        endpoint = dict(getattr(pi, "endpoint_check", {}) or {})
        return int(endpoint.get("interval_days", 7))

    async def _due_jobs(
        self, now: Any, interval_days: int, limit: int, *, force: bool
    ) -> list[str]:
        cutoff = now - timedelta(days=max(0, interval_days))
        due: list[str] = []
        page = 100
        offset = 0
        async with self._container.uow_factory() as uow:
            suppressed = set(await uow.suppressions.active_job_ids(now))
            while len(due) < limit:
                rows = await uow.jobs.list(limit=page, offset=offset)
                if not rows:
                    break
                candidates = [
                    row
                    for row in rows
                    if str(row.get("posting_status") or "active") == "active"
                    and row.get("canonical_url")
                    and str(row["id"]) not in suppressed
                ]
                if candidates:
                    latest = await uow.intelligence.endpoints.latest_for_jobs(
                        [str(row["id"]) for row in candidates]
                    )
                    for row in candidates:
                        job_id = str(row["id"])
                        if not force:
                            last = latest.get(job_id)
                            if last is not None:
                                observed = _parse_dt(last.get("observed_at"))
                                if observed is not None and observed > cutoff:
                                    continue
                        due.append(job_id)
                        if len(due) >= limit:
                            break
                if len(rows) < page:
                    break
                offset += page
            await uow.commit()
        return due

    async def _check_one(self, job_id: str, now: Any) -> str:
        async with self._container.uow_factory() as uow:
            job = await uow.jobs.get(job_id)
            previous = await uow.intelligence.endpoints.latest(job_id)
            await uow.commit()
        if job is None:
            raise PortError(
                code="not_found", message=f"unknown job: {job_id}", retryable=False
            )
        url = str(job.get("canonical_url") or "")
        raw_details = (previous or {}).get("details")
        previous_details: dict[str, Any] = (
            dict(raw_details) if isinstance(raw_details, Mapping) else {}
        )
        previous_etag = _text(previous_details.get("etag"))
        previous_modified = _text(previous_details.get("last_modified"))
        conditional: dict[str, str] = {}
        if previous_etag:
            # An ETag is the stronger validator; when it exists the request uses
            # If-None-Match only, because HTTP ignores If-Modified-Since then.
            conditional["If-None-Match"] = previous_etag
        elif previous_modified:
            conditional["If-Modified-Since"] = previous_modified
        state = "error"
        status: int | None = None
        final_url = url
        details: dict[str, Any] = {}
        if not url:
            details = {"reason": "the job has no canonical URL"}
        else:
            result = await self._http.send(
                HttpRequest(
                    method="GET",
                    url=url,
                    headers=conditional,
                    respect_robots=True,
                )
            )
            if isinstance(result, Err):
                error = result.error
                if _is_timeout(error):
                    state = "timeout"
                else:
                    state = "error"
                details = {"error_code": error.code, "error": error.message}
            else:
                response = result.value
                status = int(response.status)
                final_url = str(response.url or url)
                response_etag = _header(response.headers, "etag") or previous_etag
                response_modified = (
                    _header(response.headers, "last-modified") or previous_modified
                )
                if status == 304 and previous is not None:
                    # The page has not changed since the stored validators, so the
                    # previous state stands; the refresh still counts as a check.
                    state = str(previous.get("state") or "error")
                    body = ""
                    details = {
                        "conditional": "not_modified",
                        "reused_state": state,
                        "characters_inspected": 0,
                    }
                else:
                    body = response.body.decode("utf-8", errors="ignore")
                    state = classify_endpoint(
                        status=status,
                        final_url=final_url,
                        html=body,
                        requested_url=url,
                    )
                    details = {"characters_inspected": len(body)}
                if response_etag:
                    details["etag"] = response_etag
                if response_modified:
                    details["last_modified"] = response_modified
                if conditional:
                    details["conditional"] = details.get(
                        "conditional", "revalidated"
                    )
        record = EndpointObservationRecord(
            id=f"ep_{uuid.uuid4().hex[:16]}",
            job_id=job_id,
            observed_at=now,
            state=state,
            http_status=status,
            final_url=final_url or None,
            details=details,
        )
        async with self._container.uow_factory() as uow:
            await uow.intelligence.endpoints.add(record)
            await uow.commit()
        return state


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lowered = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered and str(value).strip():
            return str(value).strip()
    return None


def _text(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _is_timeout(error: PortError) -> bool:
    return "timeout" in error.code or "timeout" in error.message.casefold()


def _parse_dt(value: Any) -> Any:
    from datetime import datetime

    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def endpoint_summary(rows: Mapping[str, Mapping[str, Any]] | None) -> dict[str, Any]:
    """The latest state per job, safe to expose to the UI."""
    if not rows:
        return {}
    return {str(key): dict(value) for key, value in rows.items()}


__all__ = ["EndpointVerifier", "endpoint_summary"]
