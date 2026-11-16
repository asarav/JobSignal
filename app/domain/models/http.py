from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class HttpRequest:
    method: Literal["GET", "POST", "HEAD"]
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    json_body: Any | None = None
    timeout_s: float = 20.0
    max_bytes: int = 5_000_000
    respect_robots: bool = True


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    url: str
    headers: Mapping[str, str]
    body: bytes
    elapsed_ms: int
    from_cache: bool = False
