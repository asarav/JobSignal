"""IPC round trips and error envelopes for local backups (M11)."""

from __future__ import annotations

import shutil
from typing import Any

import aiosqlite
import pytest
from tests.support.containers import build_test_container


def _container(tmp_path, name: str):
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    return build_test_container(
        data_dir=str(root), sqlite_db=root / "jobs.sqlite"
    )


async def _seed(container) -> None:
    await container.uow_factory.initialize()
    async with aiosqlite.connect(container.uow_factory.db_path) as conn:
        await conn.execute(
            "INSERT INTO companies(id, canonical_name, normalized_name, created_at) "
            "VALUES ('co_a', 'Acme', 'acme', '2026-01-01T00:00:00Z')"
        )
        await conn.commit()


async def _call(dispatcher, method: str, params: dict[str, Any]) -> dict[str, Any]:
    response = await dispatcher.handle_request(
        {"id": "req_1", "method": method, "params": params}
    )
    assert response["ok"] is True, response
    return response["result"]


@pytest.mark.asyncio
async def test_backup_round_trip_over_ipc(tmp_path) -> None:
    from app.worker.main import _build_dispatcher

    source = _container(tmp_path, "source")
    await _seed(source)
    dispatcher = _build_dispatcher(source)

    exported = await _call(dispatcher, "system.export_backup", {})
    assert exported["counts"]["companies"] == 1
    history = await _call(dispatcher, "system.get_backup_history", {"limit": 5})
    assert [run["kind"] for run in history["runs"]] == ["export"]

    target = _container(tmp_path, "target")
    await target.uow_factory.initialize()
    (tmp_path / "target" / "backups").mkdir(parents=True, exist_ok=True)
    shutil.copy(exported["path"], tmp_path / "target" / "backups" / "a.json")
    target_dispatcher = _build_dispatcher(target)

    imported = await _call(
        target_dispatcher,
        "system.import_backup",
        {"path": "backups/a.json", "confirm": True},
    )
    assert imported["verified"] is True
    assert imported["counts"]["companies"] == 1


@pytest.mark.asyncio
async def test_import_needs_confirmation_and_never_overwrites() -> None:
    from app.worker.main import _build_dispatcher

    container = build_test_container()
    dispatcher = _build_dispatcher(container)

    unconfirmed = await dispatcher.handle_request(
        {
            "id": "req_2",
            "method": "system.import_backup",
            "params": {"path": "backups/missing.json"},
        }
    )
    assert unconfirmed["ok"] is False
    assert unconfirmed["error"]["code"] == "validation.confirmation_required"

    missing = await dispatcher.handle_request(
        {
            "id": "req_3",
            "method": "system.import_backup",
            "params": {"path": "backups/missing.json", "confirm": True},
        }
    )
    assert missing["ok"] is False
    assert missing["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_an_invalid_export_path_is_an_error_envelope() -> None:
    from app.worker.main import _build_dispatcher

    container = build_test_container()
    dispatcher = _build_dispatcher(container)

    response = await dispatcher.handle_request(
        {"id": "req_4", "method": "system.export_backup", "params": {"path": 5}}
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "validation.invalid_params"
