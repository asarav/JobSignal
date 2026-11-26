"""IPC fuzzing: malformed envelopes become error envelopes, never crashes.

The corpus is deterministic so a failure is reproducible. It covers validation
boundaries and the dispatcher's catch-all, not only the happy path.
"""

from __future__ import annotations

import pytest
from app.application.dto import EmptyParams
from app.domain.models.common import PortError
from app.worker.ipc.dispatcher import Dispatcher, validate_request

MALFORMED_ENVELOPES = [
    {},
    {"v": 2, "type": "request", "method": "system.hello"},
    {"v": 1, "type": "event", "method": "system.hello"},
    {"v": 1, "type": "request", "method": 123},
    {"v": 1, "type": "request", "method": "system.hello", "params": []},
    {"v": 1, "type": "request", "method": "system.hello", "params": "x"},
    {"v": None, "type": None, "method": None},
]


@pytest.mark.parametrize("envelope", MALFORMED_ENVELOPES)
def test_validate_request_rejects_malformed_envelopes(envelope) -> None:
    with pytest.raises(PortError) as caught:
        validate_request(envelope)
    assert caught.value.code in {
        "unsupported.version",
        "validation.invalid_params",
    }


async def _echo(params: EmptyParams) -> dict[str, bool]:
    return {"ok": True}


@pytest.mark.asyncio
async def test_dispatcher_returns_error_envelopes_for_fuzzed_requests() -> None:
    dispatcher = Dispatcher()
    dispatcher.register("test.echo", _echo, params_model=EmptyParams)

    nested: dict[str, object] = {"value": "x"}
    for _ in range(80):
        nested = {"nested": nested}

    requests = [
        {"id": "1", "method": None, "params": {}},
        {"id": "2", "method": "does.not.exist", "params": {}},
        {"id": "3", "method": "test.echo", "params": []},
        {"id": "4", "method": "test.echo", "params": "not-an-object"},
        {"id": "5", "method": "test.echo", "params": {"unexpected": 1}},
        {"id": "6", "method": "test.echo", "params": nested},
        {"id": "7", "method": "TEST.ECHO", "params": {}},
    ]
    for request in requests:
        response = await dispatcher.handle_request(request)
        assert response["type"] == "response"
        assert response["ok"] is False, request
        assert response["error"]["code"] in {
            "validation.invalid_params",
            "not_found",
            "internal.unexpected",
        }


@pytest.mark.asyncio
async def test_a_missing_or_null_id_is_still_a_response_not_a_crash() -> None:
    dispatcher = Dispatcher()
    dispatcher.register("test.echo", _echo, params_model=EmptyParams)
    for request in (
        {"method": "test.echo", "params": {}},
        {"id": None, "method": "test.echo", "params": {}},
    ):
        response = await dispatcher.handle_request(request)
        assert response["type"] == "response"
        assert response["ok"] is True


@pytest.mark.asyncio
async def test_dispatcher_never_raises_on_a_handler_exception() -> None:
    async def boom(params: EmptyParams) -> dict[str, bool]:
        raise RuntimeError("handler exploded")

    dispatcher = Dispatcher()
    dispatcher.register("test.boom", boom, params_model=EmptyParams)
    response = await dispatcher.handle_request(
        {"id": "1", "method": "test.boom", "params": {}}
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "internal.unexpected"
