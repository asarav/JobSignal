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
    """The worker shipped with the app, beside the binary or in an AppImage.

    An AppImage launcher (``AppRun``) sits at the bundle root while both it and
    the binaries it launches live in ``usr/bin``.
    """
    directories = [executable.parent, executable.parent / "usr" / "bin"]
    for directory in directories:
        if not directory.is_dir():
            continue
        for candidate in directory.iterdir():
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


def startup_output(process: subprocess.Popen) -> str:
    """Whatever the app printed before it was stopped, for a readable failure."""
    try:
        stdout, stderr = process.communicate(timeout=15)
    except (subprocess.TimeoutExpired, ValueError):
        return ""
    text = "\n".join(part for part in (stderr, stdout) if part)
    return "\n".join(text.strip().splitlines()[-15:])


def run_app(executable: Path, data_dir: Path, *, timeout: float) -> dict:
    # ``Popen`` runs with ``cwd=executable.parent``, so the program must be
    # absolute; the workflow finds bundles with relative paths.
    executable = executable.resolve()
    env = dict(os.environ)
    env["LODESTAR_DATA_DIR"] = str(data_dir)
    homes = data_dir.parent / "home"
    homes.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(homes)
    env["XDG_DATA_HOME"] = str(homes / ".local" / "share")
    if sys.platform.startswith("linux"):
        # WebKitGTK cannot composite without a GPU on a headless runner.
        env.setdefault("WEBKIT_DISABLE_COMPOSITING_MODE", "1")
    process = subprocess.Popen(
        [str(executable)],
        cwd=str(executable.parent),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        try:
            database = wait_for_database(data_dir, timeout=timeout)
        except SystemExit as exc:
            terminate(process)
            output = startup_output(process)
            raise SystemExit(f"{exc}\n--- app output ---\n{output or '(none)'}") from None
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


def macos_executable(app: Path) -> Path:
    """The bundle's own binary, never the worker sidecar beside it."""
    binaries = app / "Contents" / "MacOS"
    if not binaries.is_dir():
        raise SystemExit(f"{app} has no Contents/MacOS directory")
    candidates = [
        path
        for path in binaries.iterdir()
        if path.is_file() and "worker" not in path.name.lower()
    ]
    if not candidates:
        raise SystemExit(f"no application executable inside {binaries}")
    # ``Job Signal.app`` holds ``Job Signal``; fall back to the largest file.
    preferred = [path for path in candidates if path.name == app.stem]
    return preferred[0] if preferred else max(candidates, key=lambda path: path.stat().st_size)


def macos_app(app: Path, timeout: float) -> dict:
    executable = macos_executable(app)
    data_dir = Path(tempfile.mkdtemp(prefix="job-signal-macos-data-"))
    try:
        return run_app(executable, data_dir, timeout=timeout)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def artifact(path: Path, what: str) -> Path:
    resolved = path.resolve()
    if not resolved.exists():
        raise SystemExit(f"{what} not found: {path}")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--msi", type=Path)
    parser.add_argument("--appimage", type=Path)
    parser.add_argument("--macos-app", type=Path)
    parser.add_argument("--exe", type=Path, help="run an already-extracted executable")
    parser.add_argument("--timeout", type=float, default=180.0)
    args = parser.parse_args()

    # Resolve every artifact up front: the workflow passes paths from ``find``,
    # and each mode changes directory before launching it.
    if args.msi:
        result = windows_msi(artifact(args.msi, "MSI"), args.timeout)
    elif args.appimage:
        result = appimage(artifact(args.appimage, "AppImage"), args.timeout)
    elif args.macos_app:
        result = macos_app(artifact(args.macos_app, ".app bundle"), args.timeout)
    elif args.exe:
        data_dir = Path(tempfile.mkdtemp(prefix="job-signal-exe-data-"))
        result = run_app(artifact(args.exe, "executable"), data_dir, timeout=args.timeout)
    else:
        print("pass one of --msi, --appimage, --macos-app or --exe")
        return 2

    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
