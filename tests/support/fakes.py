"""Test-only fakes demonstrating adapter swapability (M0)."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.http import HttpRequest, HttpResponse
from app.domain.ports.clock import Clock, IdGenerator
from app.domain.ports.http import HttpClient
from app.domain.ports.repositories import UnitOfWorkFactory


class FakeClock(Clock):
    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 1, 1, tzinfo=UTC)
        self.sleeps: list[float] = []

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self._now = self._now + timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


class FakeIdGenerator(IdGenerator):
    def __init__(self, prefix: str = "01TEST") -> None:
        self._n = 0
        self._prefix = prefix

    def new_id(self) -> str:
        self._n += 1
        return f"{self._prefix}{self._n:020d}"[:26]


class FakeHttpClient(HttpClient):
    def __init__(self, responses: dict[str, HttpResponse] | None = None) -> None:
        self._responses = responses or {}
        self.sent: list[HttpRequest] = []
        self.block_socket = False

    async def send(self, req: HttpRequest) -> Result[HttpResponse, PortError]:
        if self.block_socket:
            return Err(PortError(code="network.offline", message="socket blocked", retryable=False))
        self.sent.append(req)
        if req.url in self._responses:
            return Ok(self._responses[req.url])
        return Ok(HttpResponse(status=200, url=req.url, headers={}, body=b"ok", elapsed_ms=1))


class FakeBrowserProvider:
    id = "browser.fake"
    version = "1.0.0"

    def __init__(
        self,
        form_model=None,
        html: str | None = None,
        robots_allowed: bool = True,
    ) -> None:
        self._form_model = form_model
        self._html = html
        self._robots_allowed = robots_allowed

    async def open(self, url: str, opts) -> Result:
        from app.domain.models.browser import BrowserPage
        from app.domain.models.common import Ok

        return Ok(BrowserPage(session_id="s1", url=url))

    async def rendered_html(self, session_id: str) -> str | None:
        return self._html

    async def robots_allowed(self, url: str) -> bool:
        return self._robots_allowed

    async def close_session(self, session_id: str) -> None:
        return None

    async def close_all(self) -> None:
        return None


class FakeEventPublisher:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish(
        self,
        topic: str,
        payload: Mapping[str, Any],
        *,
        task_id: str | None = None,
    ) -> None:
        self.events.append({"topic": topic, "payload": dict(payload), "task_id": task_id})


class FakeStructuredLogger:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def bind(self, **fields):
        return self

    def debug(self, event, **fields):
        self.events.append({"level": "debug", "event": event, **fields})

    def info(self, event, **fields):
        self.events.append({"level": "info", "event": event, **fields})

    def warning(self, event, **fields):
        self.events.append({"level": "warning", "event": event, **fields})

    def error(self, event, **fields):
        self.events.append({"level": "error", "event": event, **fields})


class FakeUnitOfWorkFactory(UnitOfWorkFactory):
    def __init__(self, uow) -> None:
        self._uow = uow

    def __call__(self):
        return self._uow