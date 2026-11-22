"""The shell's per-method request timeout (spec §15.4, §17.2).

A crawl, a discovery pass or a matching run is not bounded by one HTTP request.
A fixed 60 s wait abandoned work that was still running and reported a failure
to the user, so the wait is declared per method and generated into Rust.
"""

from __future__ import annotations

from app.worker.ipc.method_catalog import MILESTONE_METHODS

SPECS = {spec.method: spec for spec in MILESTONE_METHODS}

#: Methods whose work is bounded by many requests, not one.
LONG_HANDLERS = (
    "discovery.run",
    "discovery.auto_discover",
    "matching.run",
)


def test_every_method_declares_a_positive_timeout() -> None:
    for spec in MILESTONE_METHODS:
        assert spec.timeout_s > 0, spec.method


def test_long_handlers_wait_longer_than_one_http_budget() -> None:
    default = SPECS["system.health"].timeout_s
    for method in LONG_HANDLERS:
        assert SPECS[method].timeout_s >= 5 * default, method


def test_background_polls_stay_prompt() -> None:
    for method in ("system.health", "tasks.list", "settings.get", "jobs.list"):
        assert SPECS[method].timeout_s == 60.0, method
