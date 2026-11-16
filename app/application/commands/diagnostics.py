"""Diagnostics use cases (sandboxed URL fetch + no-op sleep task)."""

from __future__ import annotations

from urllib.parse import urlparse

from app.application.container import Container
from app.application.dto import FetchUrlParams, FetchUrlResult, SleepTaskParams
from app.domain.models.common import Err, PortError
from app.domain.models.http import HttpRequest


class FetchUrlUseCase:
    """Single-request diagnostics probe. This is not a crawler.

    M1 owns robots handling, rate limits and retries. M0 only proves that the
    HttpClient port is replaceable and that no socket is opened when a fake is
    substituted (swapability S1).
    """

    def __init__(self, container: Container) -> None:
        self._container = container

    async def execute(self, params: FetchUrlParams) -> FetchUrlResult:
        scheme = urlparse(params.url).scheme.lower()
        if scheme not in {"http", "https"}:
            raise PortError(
                code="validation.invalid_params",
                message="only http/https URLs may be fetched",
                details={"scheme": scheme or None},
            )
        if self._container.http is None:
            raise PortError(
                code="plugin.unavailable",
                message="no HttpClient adapter is configured",
            )
        result = await self._container.http.send(HttpRequest(method="GET", url=params.url))
        if isinstance(result, Err):
            raise result.error
        resp = result.value
        return FetchUrlResult(
            status=resp.status,
            url=resp.url,
            elapsed_ms=resp.elapsed_ms,
            bytes=len(resp.body),
        )


class DiagnosticsSleepUseCase:
    """No-op task used to exercise progress, completion, and cancellation."""

    def __init__(self, container: Container) -> None:
        self._container = container

    async def run(self, params: SleepTaskParams, *, task_id: str) -> dict[str, object]:
        for step in range(max(params.steps, 1)):
            await self._container.clock.sleep(params.seconds)
            self._container.events.publish(
                "task.progress",
                {"step": step + 1, "total": max(params.steps, 1)},
                task_id=task_id,
            )
        return {"steps": max(params.steps, 1), "seconds": params.seconds}
