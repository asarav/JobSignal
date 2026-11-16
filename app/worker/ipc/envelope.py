"""IPC envelopes and error codes (protocol v1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROTOCOL_VERSION = 1

VALID_TYPES = ("request", "response", "event")

ERROR_CODES = frozenset(
    {
        "validation.invalid_params",
        "not_found",
        "conflict",
        "permission.denied",
        "unsupported.version",
        "task.cancelled",
        "task.timeout",
        "plugin.unavailable",
        "plugin.circuit_open",
        "network.offline",
        "robots.disallowed",
        "storage.locked",
        "storage.migration_required",
        "internal.unexpected",
    }
)


@dataclass
class Envelope:
    v: int = PROTOCOL_VERSION
    type: str = "request"
    id: str | None = None
    method: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    ok: bool | None = None
    result: Any = None
    error: dict[str, Any] | None = None
    topic: str | None = None
    seq: int = 0
    ts: str = ""
    task_id: str | None = None
    data: Any = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"v": self.v, "type": self.type}
        if self.id is not None:
            payload["id"] = self.id
        if self.type == "request":
            payload["method"] = self.method
            payload["params"] = self.params
            payload["meta"] = self.meta
        elif self.type == "response":
            payload["ok"] = bool(self.ok)
            if self.ok:
                payload["result"] = self.result if self.result is not None else {}
            else:
                payload["error"] = self.error or {
                    "code": "internal.unexpected",
                    "message": "unknown error",
                    "retryable": False,
                    "details": {},
                }
        elif self.type == "event":
            payload["topic"] = self.topic
            payload["seq"] = self.seq
            payload["ts"] = self.ts
            if self.task_id is not None:
                payload["task_id"] = self.task_id
            payload["data"] = self.data if self.data is not None else {}
        return payload


def error_envelope(
    request_id: str | None,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> Envelope:
    return Envelope(
        v=PROTOCOL_VERSION,
        type="response",
        id=request_id,
        ok=False,
        error={
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        },
    )


def ok_envelope(request_id: str | None, result: Any) -> Envelope:
    return Envelope(v=PROTOCOL_VERSION, type="response", id=request_id, ok=True, result=result)


def event_envelope(
    topic: str,
    data: dict[str, Any],
    *,
    seq: int,
    ts: str,
    task_id: str | None = None,
) -> Envelope:
    return Envelope(
        v=PROTOCOL_VERSION,
        type="event",
        topic=topic,
        seq=seq,
        ts=ts,
        task_id=task_id,
        data=data,
    )
