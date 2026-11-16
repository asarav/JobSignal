"""The M0 method catalogue is the single source of truth for IPC exposure."""

from __future__ import annotations

import json
from pathlib import Path

from app.worker.ipc.method_catalog import (
    FORBIDDEN_METHOD_PREFIXES,
    MILESTONE_METHOD_NAMES,
    MILESTONE_METHODS,
)
from tests.support.containers import build_test_container

ROOT = Path(__file__).resolve().parents[2]


def test_no_forbidden_prefix_in_m0_methods():
    for method in MILESTONE_METHOD_NAMES:
        for prefix in FORBIDDEN_METHOD_PREFIXES:
            assert not method.startswith(prefix), f"{method} belongs to a later milestone"


def test_dispatcher_exposes_exactly_the_catalogue():
    from app.worker.main import _build_dispatcher

    dispatcher = _build_dispatcher(build_test_container())
    assert tuple(dispatcher.allowed_methods()) == tuple(sorted(MILESTONE_METHOD_NAMES))


def test_schema_registry_matches_the_catalogue():
    catalogue = json.loads((ROOT / "schemas" / "ipc" / "methods.json").read_text("utf-8"))
    registered = tuple(entry["method"] for entry in catalogue["methods"])
    assert registered == MILESTONE_METHOD_NAMES
    assert tuple(catalogue["forbidden_prefixes"]) == FORBIDDEN_METHOD_PREFIXES
    assert catalogue["protocol_version"] == 1


def test_every_schema_file_referenced_exists():
    catalogue = json.loads((ROOT / "schemas" / "ipc" / "methods.json").read_text("utf-8"))
    for entry in catalogue["methods"]:
        for key in ("request_schema", "response_schema"):
            assert (ROOT / entry[key]).is_file(), f"missing {entry[key]}"


def test_long_running_flags():
    by_name = {spec.method: spec.long_running for spec in MILESTONE_METHODS}
    assert by_name["diagnostics.sleep"] is True
    assert by_name["system.health"] is False
