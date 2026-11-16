"""NDJSON transport over stdin/stdout with a max line size."""

from __future__ import annotations

import asyncio
import json
from typing import Any, TextIO

from app.domain.models.common import PortError
from app.worker.ipc.envelope import PROTOCOL_VERSION

MAX_LINE_BYTES = 8 * 1024 * 1024


class NdjsonTransport:
    def __init__(self, stdin: TextIO, stdout: TextIO) -> None:
        self._stdin = stdin
        self._stdout = stdout
        self._write_lock = asyncio.Lock()

    async def read(self) -> dict[str, Any] | None:
        line = await asyncio.to_thread(self._stdin.readline)
        if not line:
            return None
        if len(line.encode("utf-8")) > MAX_LINE_BYTES:
            raise PortError(
                code="validation.invalid_params",
                message=f"envelope exceeds max line size ({MAX_LINE_BYTES} bytes)",
            )
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise PortError(
                code="validation.invalid_params",
                message=f"invalid JSON envelope: {exc.msg}",
            ) from exc
        if not isinstance(value, dict):
            raise PortError(
                code="validation.invalid_params",
                message="envelope must be a JSON object",
            )
        return value

    async def write(self, obj: dict[str, Any]) -> None:
        if "v" not in obj:
            obj = {"v": PROTOCOL_VERSION, **obj}
        line = json.dumps(obj, separators=(",", ":")) + "\n"
        encoded = line.encode("utf-8")
        if len(encoded) > MAX_LINE_BYTES:
            raise PortError(
                code="internal.unexpected",
                message="response exceeds max line size",
            )
        async with self._write_lock:
            await asyncio.to_thread(self._stdout.write, line)
            await asyncio.to_thread(self._stdout.flush)
