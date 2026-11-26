#!/usr/bin/env python3
"""Crawl one real public Greenhouse board through the worker and report counts.

This is a release check, not a CI gate: it needs network access and a live board.
Run from the repository root:

    python scripts/live_crawl_smoke.py --board https://boards.greenhouse.io/figma
"""

from __future__ import annotations

import argparse
import json
import queue
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def read_until(lines: queue.Queue[str], request_id: str, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
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
        if envelope.get("type") == "response" and envelope.get("id") == request_id:
            return envelope
    raise SystemExit(f"timed out waiting for {request_id}")


def call(
    process, lines, request_id: str, method: str, params: dict, *, timeout: float = 300
) -> dict:
    assert process.stdin is not None
    process.stdin.write(
        json.dumps(
            {
                "v": 1,
                "type": "request",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        + "\n"
    )
    process.stdin.flush()
    response = read_until(lines, request_id, timeout)
    if not response.get("ok"):
        raise SystemExit(f"{method} failed: {response.get('error')}")
    return response.get("result") or {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", default="https://boards.greenhouse.io/figma")
    parser.add_argument("--timeout", type=float, default=600.0)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(
        prefix="job-signal-live-crawl-", ignore_cleanup_errors=True
    ) as data_dir:
        database = Path(data_dir) / "lodestar.sqlite"
        process = subprocess.Popen(
            [sys.executable, "-m", "app.worker.main", "--data-dir", data_dir],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        lines: queue.Queue[str] = queue.Queue()

        def _reader(stream) -> None:
            for line in stream:
                lines.put(line)
            lines.put("")

        threading.Thread(target=_reader, args=(process.stdout,), daemon=True).start()
        try:
            call(
                process,
                lines,
                "live-hello",
                "system.hello",
                {"protocol_version": 1, "ui_version": "live-crawl-smoke"},
                timeout=120,
            )
            source = call(
                process,
                lines,
                "live-add",
                "sources.add",
                {"url": args.board, "interval_s": 0},
                timeout=120,
            )
            source_id = source["source_id"]
            run = call(
                process,
                lines,
                "live-run",
                "discovery.run",
                {"source_id": source_id},
                timeout=args.timeout,
            )
            call(
                process,
                lines,
                "live-shutdown",
                "system.shutdown",
                {},
                timeout=60,
            )
            process.wait(timeout=30)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=30)

        if not database.exists():
            print("worker did not create the database")
            return 1
        with sqlite3.connect(database) as conn:
            jobs = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            crawls = conn.execute("SELECT COUNT(*) FROM crawl_runs").fetchone()[0]
            newest = conn.execute(
                "SELECT status, stats_json FROM crawl_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()

        result = {
            "ok": jobs > 0 and crawls > 0,
            "board": args.board,
            "discovered": run.get("discovered"),
            "new": run.get("new"),
            "updated": run.get("updated"),
            "skipped": run.get("skipped"),
            "errors": run.get("errors"),
            "jobs_stored": jobs,
            "crawl_runs": crawls,
            "last_crawl": {"status": newest[0], "stats": newest[1]} if newest else None,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
