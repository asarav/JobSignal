"""Integration: spawn the real worker subprocess and speak protocol v1.

These tests are the contract between the Rust supervisor and the Python worker:
NDJSON on stdin/stdout, `worker.ready`, request/response correlation, error
mapping, task persistence/cancellation, and graceful shutdown.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
TIMEOUT = 20.0


class WorkerProcess:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self._sequence = 0
        self.events: list[dict[str, Any]] = []
        self._stderr_task = asyncio.create_task(self._drain_stderr())

    async def _drain_stderr(self) -> None:
        assert self.proc.stderr is not None
        while True:
            line = await self.proc.stderr.readline()
            if not line:
                return

    async def send(self, envelope: dict[str, Any]) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write((json.dumps(envelope, separators=(",", ":")) + "\n").encode())
        await self.proc.stdin.drain()

    async def read(self, timeout: float = TIMEOUT) -> dict[str, Any]:
        assert self.proc.stdout is not None
        line = await asyncio.wait_for(self.proc.stdout.readline(), timeout)
        assert line, "worker closed stdout unexpectedly"
        return json.loads(line)

    async def wait_for(self, predicate, timeout: float = TIMEOUT) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for worker message")
            message = await self.read(remaining)
            if predicate(message):
                return message
            if message.get("type") == "event":
                self.events.append(message)

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._sequence += 1
        request_id = f"it-{self._sequence}"
        await self.send(
            {
                "v": 1,
                "type": "request",
                "id": request_id,
                "method": method,
                "params": params or {},
                "meta": {},
            }
        )
        return await self.wait_for(
            lambda message: message.get("type") == "response"
            and message.get("id") == request_id
        )

    async def close(self) -> None:
        if self.proc.returncode is None:
            self.proc.kill()
            await self.proc.wait()
        self._stderr_task.cancel()


def _env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + existing if existing else "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


async def spawn_worker(data_dir: Path) -> WorkerProcess:
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "app.worker.main",
        "--data-dir",
        str(data_dir),
        cwd=str(ROOT),
        env=_env(),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    worker = WorkerProcess(proc)
    ready = await worker.wait_for(
        lambda message: message.get("type") == "event"
        and message.get("topic") == "worker.ready"
    )
    assert ready["v"] == 1
    assert ready["data"]["protocol_versions"] == [1]
    return worker


@pytest.mark.asyncio
async def test_handshake_hello_health(tmp_path):
    worker = await spawn_worker(tmp_path / "data")
    try:
        hello = await worker.request(
            "system.hello", {"protocol_version": 1, "ui_version": "integration"}
        )
        assert hello["ok"] is True
        result = hello["result"]
        assert result["protocol_version"] == 1
        assert result["schema_version"] >= 1
        for method in ("system.hello", "system.health", "settings.get", "plugins.list"):
            assert method in result["capabilities"]

        health = await worker.request("system.health")
        assert health["ok"] is True
        assert health["result"]["status"] == "healthy"
        assert Path(health["result"]["data_dir"]).resolve() == (tmp_path / "data").resolve()
        assert health["result"]["schema_version"] >= 1
        assert isinstance(health["result"]["plugin_health"], list)
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_invalid_params_are_mapped(tmp_path):
    worker = await spawn_worker(tmp_path / "data")
    try:
        missing = await worker.request("system.hello", {})
        assert missing["ok"] is False
        assert missing["error"]["code"] == "validation.invalid_params"

        wrong_type = await worker.request(
            "system.hello", {"protocol_version": "one", "ui_version": "x"}
        )
        assert wrong_type["ok"] is False
        assert wrong_type["error"]["code"] == "validation.invalid_params"

        version = await worker.request(
            "system.hello", {"protocol_version": 2, "ui_version": "x"}
        )
        assert version["ok"] is False
        assert version["error"]["code"] == "unsupported.version"
    finally:
        await worker.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        "discovery.run",
        "jobs.list",
        "browser.open",
        "resume.parse",
        "matching.score",
        "applications.create",
        "companies.list",
        "posting_intelligence.evaluate",
        "keywords.extract",
        "search_profiles.list",
        "totally.unknown",
    ],
)
async def test_forbidden_and_unknown_methods_are_rejected(tmp_path, method):
    worker = await spawn_worker(tmp_path / "data")
    try:
        response = await worker.request(method, {})
        assert response["ok"] is False
        assert response["error"]["code"] == "not_found"
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_settings_roundtrip_and_immutable_guard(tmp_path):
    worker = await spawn_worker(tmp_path / "data")
    try:
        updated = await worker.request(
            "settings.update", {"patch": {"logging": {"level": "warning"}}}
        )
        assert updated["ok"] is True
        assert updated["result"]["config"]["logging"]["level"] == "warning"

        fetched = await worker.request("settings.get")
        assert fetched["result"]["overrides"]["logging"]["level"] == "warning"

        blocked = await worker.request(
            "settings.update", {"patch": {"paths": {"data_dir": "/somewhere/else"}}}
        )
        assert blocked["ok"] is False
        assert blocked["error"]["code"] == "permission.denied"

        invalid = await worker.request(
            "settings.update", {"patch": {"http": {"nope": 1}}}
        )
        assert invalid["ok"] is False
        assert invalid["error"]["code"] == "validation.invalid_params"
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_plugins_list_includes_builtin(tmp_path):
    worker = await spawn_worker(tmp_path / "data")
    try:
        response = await worker.request("plugins.list")
        assert response["ok"] is True
        plugins = {plugin["plugin_id"]: plugin for plugin in response["result"]["plugins"]}
        assert "extract.demo" in plugins
        assert plugins["extract.demo"]["enabled"] is True

        disabled = await worker.request("plugins.disable", {"plugin_id": "extract.demo"})
        assert disabled["ok"] is True
        listed = await worker.request("plugins.list")
        by_id = {plugin["plugin_id"]: plugin for plugin in listed["result"]["plugins"]}
        assert by_id["extract.demo"]["enabled"] is False

        caps = await worker.request(
            "plugins.get_capabilities", {"plugin_id": "extract.demo"}
        )
        assert caps["result"]["capabilities"]["extract.demo"] == ["extract"]
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_task_progress_completion_and_persistence(tmp_path):
    data_dir = tmp_path / "data"
    worker = await spawn_worker(data_dir)
    try:
        accepted = await worker.request("diagnostics.sleep", {"seconds": 0.0, "steps": 3})
        assert accepted["ok"] is True
        task_id = accepted["result"]["task_id"]

        completed = await worker.wait_for(
            lambda message: message.get("topic") == "task.completed"
            and message.get("task_id") == task_id,
            timeout=30,
        )
        assert completed["data"]["steps"] == 3

        listed = await worker.request("tasks.list", {"limit": 10})
        tasks = {task["id"]: task for task in listed["result"]["tasks"]}
        assert tasks[task_id]["status"] == "completed"
        assert tasks[task_id]["progress"]["step"] == 3
    finally:
        await worker.close()

    # A fresh process on the same data dir must see the persisted task.
    worker = await spawn_worker(data_dir)
    try:
        listed = await worker.request("tasks.list", {"limit": 10})
        assert task_id in {task["id"] for task in listed["result"]["tasks"]}
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_task_cancellation(tmp_path):
    worker = await spawn_worker(tmp_path / "data")
    try:
        accepted = await worker.request("diagnostics.sleep", {"seconds": 0.25, "steps": 40})
        task_id = accepted["result"]["task_id"]

        await worker.wait_for(
            lambda message: message.get("topic") == "task.progress"
            and message.get("task_id") == task_id
        )

        cancelled = await worker.request("tasks.cancel", {"task_id": task_id})
        assert cancelled["ok"] is True

        event = await worker.wait_for(
            lambda message: message.get("topic") == "task.cancelled"
            and message.get("task_id") == task_id,
            timeout=30,
        )
        assert event["task_id"] == task_id

        listed = await worker.request("tasks.list", {"limit": 10})
        statuses = {task["id"]: task["status"] for task in listed["result"]["tasks"]}
        assert statuses[task_id] == "cancelled"

        # Cancelling an unknown task is a cooperative no-op success.
        unknown = await worker.request("tasks.cancel", {"task_id": "does-not-exist"})
        assert unknown["ok"] is True
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_diagnostics_fetch_url_rejects_bad_scheme(tmp_path):
    worker = await spawn_worker(tmp_path / "data")
    try:
        response = await worker.request(
            "diagnostics.fetch_url", {"url": "file:///etc/passwd"}
        )
        assert response["ok"] is False
        assert response["error"]["code"] == "validation.invalid_params"
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_graceful_shutdown(tmp_path):
    worker = await spawn_worker(tmp_path / "data")
    response = await worker.request("system.shutdown")
    assert response["ok"] is True
    await asyncio.wait_for(worker.proc.wait(), timeout=TIMEOUT)
    assert worker.proc.returncode == 0
    worker._stderr_task.cancel()
