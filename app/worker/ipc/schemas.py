"""Method allowlist: registers M0 IPC methods with Pydantic-param validation."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:

    MethodHandler = Callable[[Any], Any]


class MethodSpec:
    def __init__(
        self,
        handler: MethodHandler,
        params_model: type | None = None,
        result_model: type | None = None,
    ) -> None:
        self.handler = handler
        self.params_model = params_model
        self.result_model = result_model

    async def handle(self, params: dict[str, Any]) -> Any:
        if self.params_model is not None:
            params = self.params_model(**params)
        return await self.handler(params)


# method -> MethodSpec, populated by dispatcher.register()
METHODS: dict[str, MethodSpec] = {}


def register(
    method: str,
    handler: MethodHandler,
    *,
    params_model: type | None = None,
    result_model: type | None = None,
) -> None:
    METHODS[method] = MethodSpec(handler, params_model, result_model)


def allowed_methods() -> list[str]:
    return sorted(METHODS)