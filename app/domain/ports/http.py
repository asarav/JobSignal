from __future__ import annotations

from typing import Protocol

from app.domain.models.common import PortError, Result
from app.domain.models.http import HttpRequest, HttpResponse


class HttpClient(Protocol):
    async def send(self, req: HttpRequest) -> Result[HttpResponse, PortError]: ...
