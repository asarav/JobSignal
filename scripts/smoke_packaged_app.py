#!/usr/bin/env python3
"""Launch a packaged build against an empty data directory and verify startup.

Windows: administratively extract the MSI, run the extracted executable.
Linux: extract the AppImage with ``--appimage-extract`` and run its AppRun.
macOS: launch the executable inside the ``.app`` bundle.

Each mode waits for ``lodestar.sqlite`` and the sidecar beside the executable,
prints the observed schema version, and terminates the app. The window may open
briefly; that is expected.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def wait_for_database(data_dir: Path, *, timeout: float) -> Path:
    target = data_dir / "lodestar.sqlite"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if target.is_file() and target.stat().st_size > 0:
            # Give migrations a moment to commit before reading.
            time.sleep(1.5)
            return target
        time.sleep(0.5)
    raise SystemExit(f"database was not created under {data_dir} within {timeout:.0f}s")


def schema_version(database: Path) -> int:
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as conn:
            row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            return int(row[0] or 0) if row else 0
    except sqlite3.Error as exc:
        return f"unreadable: {exc}"  # type: ignore[return-value]


def sidecar_beside(executable: Path) -> Path | None:
    for candidate in executable.parent.iterdir():
        if candidate.is_file() and candidate.name.startswith("lodestar-worker"):
            return candidate
    return None


def terminate(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            check=False,
        )
    else:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


def run_app(executable: Path, data_dir: Path, *, timeout: float) -> dict:
    env = dict(os.environ)
    env["LODESTAR_DATA_DIR"] = str(data_dir)
    homes = data_dir.parent / "home"
    homes.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(homes)
    env["XDG_DATA_HOME"] = str(homes / ".local" / "share")
    process = subprocess.Popen(
        [str(executable)],
        cwd=str(executable.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        database = wait_for_database(data_dir, timeout=timeout)
        sidecar = sidecar_beside(executable)
        result = {
            "ok": sidecar is not None,
            "executable": str(executable),
            "data_dir": str(data_dir),
            "database": str(database),
            "schema_version": schema_version(database),
            "sidecar": str(sidecar) if sidecar else None,
        }
        if sidecar is None:
            result["error"] = "worker sidecar is not beside the executable"
        return result
    finally:
        terminate(process)


def find_exe(root: Path) -> Path:
    candidates = [
        path
        for path in root.rglob("*.exe")
        if path.is_file()
        and "uninstall" not in path.name.lower()
        and "worker" not in path.name.lower()
    ]
    # Prefer the product binary, then the largest remaining executable.
    for path in candidates:
        if "job signal" in path.name.lower() or "lodestar-tauri" in path.name.lower():
            return path
    if candidates:
        return max(candidates, key=lambda path: path.stat().st_size)
    raise SystemExit(f"no application executable found under {root}")


def windows_msi(msi: Path, timeout: float) -> dict:
    extract_root = Path(tempfile.mkdtemp(prefix="job-signal-msi-"))
    command = [
        "msiexec",
        "/a",
        str(msi),
        "/qn",
        f"TARGETDIR={extract_root}",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        shutil.rmtree(extract_root, ignore_errors=True)
        raise SystemExit(
            f"msiexec administrative extract failed ({completed.returncode}): "
            f"{completed.stderr or completed.stdout}"
        )
    executable = find_exe(extract_root)
    data_dir = extract_root / "smoke-data"
    data_dir.mkdir()
    try:
        return run_app(executable, data_dir, timeout=timeout)
    finally:
        shutil.rmtree(extract_root, ignore_errors=True)


def appimage(image: Path, timeout: float) -> dict:
    root = Path(tempfile.mkdtemp(prefix="job-signal-appimage-"))
    try:
        completed = subprocess.run(
            [str(image), "--appimage-extract"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "APPIMAGE_EXTRACT_AND_RUN": "1"},
        )
        if completed.returncode != 0:
            raise SystemExit(f"AppImage extraction failed: {completed.stderr}")
        app_run = root / "squashfs-root" / "AppRun"
        executable = app_run if app_run.is_file() else find_exe(root / "squashfs-root")
        data_dir = root / "smoke-data"
        data_dir.mkdir()
        return run_app(executable, data_dir, timeout=timeout)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def macos_app(app: Path, timeout: float) -> dict:
    binaries = app / "Contents" / "MacOS"
    candidates = [path for path in binaries.iterdir() if path.is_file()]
    if not candidates:
        raise SystemExit(f"no executable inside {binaries}")
    data_dir = Path(tempfile.mkdtemp(prefix="job-signal-macos-data-"))
    try:
        return run_app(candidates[0], data_dir, timeout=timeout)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--msi", type=Path)
    parser.add_argument("--appimage", type=Path)
    parser.add_argument("--macos-app", type=Path)
    parser.add_argument("--exe", type=Path, help="run an already-extracted executable")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    if args.msi:
        result = windows_msi(args.msi, args.timeout)
    elif args.appimage:
        result = appimage(args.appimage, args.timeout)
    elif args.macos_app:
        result = macos_app(args.macos_app, args.timeout)
    elif args.exe:
        data_dir = Path(tempfile.mkdtemp(prefix="job-signal-exe-data-"))
        result = run_app(args.exe, data_dir, timeout=args.timeout)
    else:
        print("pass one of --msi, --appimage, --macos-app or --exe")
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
