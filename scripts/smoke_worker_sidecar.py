#!/usr/bin/env python3
"""Smoke-test a frozen worker sidecar over the real NDJSON protocol.

Starts the sidecar with a temporary data directory, waits for ``worker.ready``,
calls ``system.hello``, then shuts it down. Used by desktop-build.yml after each
bundle and runnable locally:

    python scripts/smoke_worker_sidecar.py --target x86_64-pc-windows-msvc
"""

from __future__ import annotations

import argparse
import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFIX = "lodestar-worker"


def find_sidecar(target: str | None) -> Path:
    roots = []
    if target:
        roots.append(ROOT / "frontend" / "src-tauri" / "target" / target / "release")
    roots.extend(
        [
            ROOT / "frontend" / "src-tauri" / "target" / "release",
            ROOT / "frontend" / "src-tauri" / "binaries",
        ]
    )
    candidates: list[Path] = []
    for root in roots:
        if not root.is_dir():
            continue
        candidates.extend(
            path
            for path in root.iterdir()
            if path.is_file() and path.name.startswith(PREFIX)
        )
    if not candidates:
        print(f"no {PREFIX}* binary found under: {', '.join(map(str, roots))}")
        raise SystemExit(2)
    exact = [path for path in candidates if path.stem == PREFIX]
    return sorted(exact or candidates)[0]


def _reader(stream, lines: queue.Queue[str]) -> None:
    for line in stream:
        lines.put(line)
    lines.put("")


def wait_for(
    lines: queue.Queue[str], predicate, *, timeout: float, description: str
) -> dict:
    deadline = time.monotonic() + timeout
    seen: list[dict] = []
    while time.monotonic() < deadline:
        try:
            line = lines.get(timeout=max(0.1, deadline - time.monotonic()))
        except queue.Empty:
            break
        if not line:
            break
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError:
            continue
        seen.append(envelope)
        if predicate(envelope):
            return envelope
    print(f"timed out waiting for {description}; saw {seen[-3:]}")
    raise SystemExit(1)


def send(process: subprocess.Popen, envelope: dict) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(envelope) + "\n")
    process.stdin.flush()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sidecar", type=Path)
    parser.add_argument("--target", help="Rust target triple used in the build path")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    sidecar = args.sidecar or find_sidecar(args.target)
    if not sidecar.is_file():
        print(f"sidecar not found: {sidecar}")
        return 2
    sidecar.chmod(sidecar.stat().st_mode | 0o111)

    with tempfile.TemporaryDirectory(prefix="job-signal-sidecar-") as data_dir:
        process = subprocess.Popen(
            [str(sidecar), "--data-dir", data_dir],
            cwd=data_dir,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        lines: queue.Queue[str] = queue.Queue()
        threading.Thread(target=_reader, args=(process.stdout, lines), daemon=True).start()

        ready = wait_for(
            lines,
            lambda item: item.get("type") == "event"
            and item.get("topic") == "worker.ready",
            timeout=args.timeout,
            description="worker.ready",
        )
        send(
            process,
            {
                "v": 1,
                "type": "request",
                "id": "smoke-hello",
                "method": "system.hello",
                "params": {"protocol_version": 1, "ui_version": "sidecar-smoke"},
            },
        )
        hello = wait_for(
            lines,
            lambda item: item.get("type") == "response"
            and item.get("id") == "smoke-hello",
            timeout=60,
            description="system.hello response",
        )
        if not hello.get("ok"):
            print(f"system.hello failed: {hello}")
            return 1
        result = hello.get("result") or {}
        send(
            process,
            {
                "v": 1,
                "type": "request",
                "id": "smoke-shutdown",
                "method": "system.shutdown",
                "params": {},
            },
        )
        wait_for(
            lines,
            lambda item: item.get("type") == "response"
            and item.get("id") == "smoke-shutdown",
            timeout=60,
            description="system.shutdown response",
        )
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            print("worker did not exit after shutdown")
            return 1

        print(
            json.dumps(
                {
                    "ok": True,
                    "sidecar": str(sidecar),
                    "schema_version": result.get("schema_version"),
                    "protocol_version": result.get("protocol_version"),
                    "worker_ready_schema": (ready.get("data") or {}).get(
                        "schema_version"
                    ),
                    "exit_code": process.returncode,
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
