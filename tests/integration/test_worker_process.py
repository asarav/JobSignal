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
                    "browser.open",
            "matching.score",
            "browser.submit",
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


@pytest.mark.asyncio
async def test_search_profiles_lifecycle(tmp_path):
    worker = await spawn_worker(tmp_path / "data")
    try:
        # Initially empty
        listed = await worker.request("search_profiles.list", {})
        assert listed["ok"] is True
        assert listed["result"]["profiles"] == []

        # Save a profile
        saved = await worker.request(
            "search_profiles.save",
            {
                "name": "Python Remote Jobs",
                "hard_filters": {"workplace_types": ["remote"], "employment_types": ["full-time"]},
                "preferences": {"sort": "newest"},
            },
        )
        assert saved["ok"] is True
        profile_id = saved["result"]["profile_id"]
        assert profile_id is not None
        assert saved["result"]["name"] == "Python Remote Jobs"
        assert saved["result"]["hard_filters"]["workplace_types"] == ["remote"]

        # List should now contain it
        listed = await worker.request("search_profiles.list", {})
        assert listed["ok"] is True
        assert len(listed["result"]["profiles"]) == 1
        assert listed["result"]["profiles"][0]["profile_id"] == profile_id
        assert listed["result"]["profiles"][0]["name"] == "Python Remote Jobs"

        # Save again with same id -> update
        updated = await worker.request(
            "search_profiles.save",
            {
                "profile_id": profile_id,
                "name": "Python Remote Jobs (Updated)",
                "hard_filters": {"workplace_types": ["remote"]},
            },
        )
        assert updated["ok"] is True
        assert updated["result"]["profile_id"] == profile_id
        assert updated["result"]["name"] == "Python Remote Jobs (Updated)"

        listed = await worker.request("search_profiles.list", {})
        assert len(listed["result"]["profiles"]) == 1
        assert listed["result"]["profiles"][0]["name"] == "Python Remote Jobs (Updated)"

        # Delete
        deleted = await worker.request(
            "search_profiles.delete", {"profile_id": profile_id}
        )
        assert deleted["ok"] is True
        assert deleted["result"]["ok"] is True

        listed = await worker.request("search_profiles.list", {})
        assert listed["ok"] is True
        assert listed["result"]["profiles"] == []

        # Deleting unknown is a no-op success
        deleted = await worker.request(
            "search_profiles.delete", {"profile_id": "does-not-exist"}
        )
        assert deleted["ok"] is True
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_auto_discover_round_trip(tmp_path):
    worker = await spawn_worker(tmp_path / "data")
    try:
        # Disable every builtin provider so this test never touches a network.
        for plugin_id in (
            "discovery.ats_directory",
            "discovery.web_index",
            "discovery.domain_index",
            "discovery.oracle_recruiting",
            "discovery.funnel",
        ):
            disabled = await worker.request("plugins.disable", {"plugin_id": plugin_id})
            assert disabled["ok"] is True

        response = await worker.request("discovery.auto_discover", {})
        assert response["ok"] is True
        result = response["result"]
        assert result["discovered"] == 0
        assert result["registered"] == 0
        assert result["crawled"] == 0
        assert result["failed"] == 0
    finally:
        await worker.close()


@pytest.mark.asyncio
async def test_resume_parse_skill_status_and_keywords(tmp_path):
    worker = await spawn_worker(tmp_path / "data")
    try:
        resume_path = tmp_path / "resume.txt"
        resume_path.write_text(
            "Jane Doe\n\nSkills\nPython, SQL\n\nExperience\nAcme\nEngineer 2020 - 2023\n"
            "- Built Python services.\n",
            encoding="utf-8",
        )
        imported = await worker.request(
            "resume.import", {"file_path": str(resume_path), "label": "v1"}
        )
        assert imported["ok"] is True
        version_id = imported["result"]["version_id"]

        parsed = await worker.request("resume.parse", {"resume_version_id": version_id})
        assert parsed["ok"] is True
        assert "python" in parsed["result"]["skills"]

        updated = await worker.request(
            "resume.set_skill_status",
            {"profile_id": "default", "keyword_id": "python", "status": "learning"},
        )
        assert updated["ok"] is True
        profile = await worker.request("resume.get_profile", {"profile_id": "default"})
        skills = {row["keyword_id"]: row for row in profile["result"]["profile"]["skills"]}
        assert skills["python"]["status"] == "learning"

        keywords = await worker.request(
            "keywords.extract", {"text": "We use Python and AWS."}
        )
        assert keywords["ok"] is True
        assert "python" in keywords["result"]["keywords"]
        assert "aws" in keywords["result"]["keywords"]
    finally:
        await worker.close()
