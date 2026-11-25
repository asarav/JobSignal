#!/usr/bin/env python3
"""Build the Python worker as a standalone Tauri sidecar.

PyInstaller freezes the worker and its data files into one executable. Tauri
expects external binaries named ``<name>-<target-triple>[.exe]`` so the bundle
can select the right file for the host platform.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
TAURI_DIR = ROOT / "frontend" / "src-tauri"
BINARIES = TAURI_DIR / "binaries"
WORK = ROOT / "build" / "pyinstaller"
TAXONOMY = ROOT / "data" / "taxonomy"


def fail(message: str) -> NoReturn:
    print(f"[worker-build] error: {message}", file=sys.stderr)
    raise SystemExit(1)


def sidecar_name(triple: str) -> str:
    """The exact file name Tauri resolves for an external binary target."""
    return f"lodestar-worker-{triple}{'.exe' if 'windows' in triple else ''}"


def host_triple() -> str:
    rustc = shutil.which("rustc")
    if rustc is None:
        fail("rustc is required to determine the Tauri target triple.")
    result = subprocess.run([rustc, "-vV"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        fail(result.stderr.strip() or "rustc -vV failed.")
    for line in result.stdout.splitlines():
        if line.startswith("host: "):
            return line.removeprefix("host: ").strip()
    fail("rustc -vV did not report a host triple.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        help=(
            "Rust target triple to name the sidecar for. Must match this host; "
            "PyInstaller cannot cross-compile, so run each target natively."
        ),
    )
    args = parser.parse_args()

    host = host_triple()
    triple = args.target or host
    if triple != host:
        fail(
            f"target {triple} does not match this host ({host}); PyInstaller cannot "
            "cross-compile. Build on a native runner for each target triple."
        )

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        fail('PyInstaller is not installed. Run: python -m pip install -e ".[build]"')

    suffix = ".exe" if "windows" in triple else ""
    expected = BINARIES / sidecar_name(triple)

    BINARIES.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(parents=True, exist_ok=True)
    for stale in BINARIES.glob("lodestar-worker-*"):
        stale.unlink()

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        "lodestar-worker",
        "--distpath",
        str(BINARIES),
        "--workpath",
        str(WORK),
        "--specpath",
        str(WORK),
        "--paths",
        str(ROOT),
        "--collect-all",
        "app",
        "--add-data",
        f"{TAXONOMY / 'v1.json'}:data/taxonomy",
        "--hidden-import",
        "aiosqlite",
        "--hidden-import",
        "pymupdf",
        "--hidden-import",
        "docx",
    ]
    # PyMuPDF exposes optional table/layout helpers with heavy scientific
    # imports. The worker only extracts text and images, so excluding those
    # keeps the sidecar small and the build deterministic.
    for module in (
        "torch",
        "pandas",
        "scipy",
        "matplotlib",
        "numba",
        "llvmlite",
        "tensorboard",
        "IPython",
        "pytest",
        "tkinter",
    ):
        command.extend(["--exclude-module", module])
    command.append(str(ROOT / "scripts" / "worker_entry.py"))
    print("[worker-build] " + " ".join(command), flush=True)
    result = subprocess.run(command, cwd=ROOT, check=False)
    if result.returncode != 0:
        return result.returncode

    produced = BINARIES / f"lodestar-worker{suffix}"
    if not produced.is_file():
        fail(f"PyInstaller did not produce {produced}")
    produced.replace(expected)
    size_mb = expected.stat().st_size / (1024 * 1024)
    print(f"[worker-build] {expected} ({size_mb:.1f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
