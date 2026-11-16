"""IPC dispatcher: method -> handler, param validation, error mapping."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ValidationError

from app.domain.models.common import PortError
from app.worker.ipc.envelope import PROTOCOL_VERSION, error_envelope, ok_envelope


class Dispatcher:
    def __init__(self) -> None:
        self._methods: dict[
            str, tuple[Callable[[Any], Awaitable[Any]], type[BaseModel] | None]
        ] = {}

    def register(
        self,
        method: str,
        handler: Callable[[Any], Awaitable[Any]],
        *,
        params_model: type[BaseModel] | None = None,
    ) -> None:
        self._methods[method] = (handler, params_model)

    def allowed_methods(self) -> list[str]:
        return sorted(self._methods)

    async def dispatch(self, method: str | None, params: dict[str, Any]) -> Any:
        if method is None:
            raise PortError(code="validation.invalid_params", message="method is required")
        entry = self._methods.get(method)
        if entry is None:
            raise PortError(code="not_found", message=f"unknown method: {method}")
        handler, params_model = entry
        validated: Any = params
        if params_model is not None:
            try:
                validated = params_model.model_validate(params)
            except ValidationError as exc:
                raise PortError(
                    code="validation.invalid_params",
                    message="invalid params",
                    retryable=False,
                    details={"errors": exc.errors(include_url=False)},
                ) from exc
        return await handler(validated)

    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        try:
            result = await self.dispatch(request.get("method"), request.get("params") or {})
            return ok_envelope(request_id, result).to_dict()
        except PortError as exc:
            return error_envelope(
                request_id,
                exc.code,
                exc.message,
                retryable=exc.retryable,
                details=dict(exc.details),
            ).to_dict()
        except Exception as exc:  # noqa: BLE001 - never leak an unhandled exception
            return error_envelope(
                request_id,
                "internal.unexpected",
                str(exc),
                retryable=False,
            ).to_dict()


def validate_request(envelope: dict[str, Any]) -> None:
    if envelope.get("v") != PROTOCOL_VERSION:
        raise PortError(
            code="unsupported.version",
            message=f"unsupported envelope version {envelope.get('v')!r}",
            details={"supported_protocol_version": PROTOCOL_VERSION},
        )
    if envelope.get("type") != "request":
        raise PortError(
            code="validation.invalid_params",
            message="only request envelopes are accepted on stdin",
        )
    if not isinstance(envelope.get("method"), str):
        raise PortError(code="validation.invalid_params", message="method must be a string")
    params = envelope.get("params", {})
    if params is not None and not isinstance(params, dict):
        raise PortError(code="validation.invalid_params", message="params must be an object")
