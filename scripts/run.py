#!/usr/bin/env python3
"""Run Job Signal on Windows, Linux, or macOS.

Modes:
  desktop  Launch the Tauri desktop app (default).
  web      Run the Vite UI in sample-data preview mode.
  build    Build a native installer for the current operating system.
  doctor   Report the local toolchain without installing anything.

The script uses only the Python standard library. It keeps the worker's dev
database in .lodestar-data/ so every platform behaves the same, and it updates
the migration checksum marker before launch so an old dev schema cannot leave
the worker in a crash loop.

Usage:
  python scripts/run.py [desktop|web|build|doctor] [options]

Windows shortcuts: run.cmd or run.ps1
macOS/Linux shortcut: ./run.sh
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
VENV = ROOT / ".venv"
DEFAULT_DATA_DIR = ROOT / ".lodestar-data"
MIGRATIONS = ROOT / "app" / "infrastructure" / "persistence" / "sqlite" / "migrations"
WORKER_IMPORTS = "import pydantic, pydantic_settings, aiosqlite, httpx, pymupdf, docx"
MIN_PYTHON = (3, 12)
MIN_NODE = 18


def log(message: str) -> None:
    print(f"[job-signal] {message}", flush=True)


def fail(message: str) -> NoReturn:
    print(f"[job-signal] error: {message}", file=sys.stderr, flush=True)
    raise SystemExit(1)


def pip_env() -> dict[str, str]:
    """Environment for ``pip install`` calls made by the launcher.

    PyMuPDF 2.x publishes no macOS Intel wheel, and its sdist builds MuPDF from
    source, which takes so long the launcher appears to hang.  Refuse the sdist
    so the failure is immediate and readable on any platform without a wheel.
    """
    env = dict(os.environ)
    env.setdefault("PIP_ONLY_BINARY", "pymupdf")
    return env


def format_command(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def run(command: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> None:
    log(format_command(command))
    try:
        subprocess.run(command, cwd=cwd, env=env, check=True)
    except FileNotFoundError as exc:
        fail(f"command not found: {exc.filename}")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc


def quiet(command: list[str], *, cwd: Path = ROOT) -> bool:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def add_cargo_to_path() -> None:
    """Cargo's default install directory is not always on a GUI shell's PATH."""
    cargo_bin = Path.home() / ".cargo" / "bin"
    if not cargo_bin.is_dir():
        return
    current = os.environ.get("PATH", "")
    parts = current.split(os.pathsep) if current else []
    if str(cargo_bin) not in parts:
        os.environ["PATH"] = os.pathsep.join([str(cargo_bin), current]).rstrip(os.pathsep)


def version_at_least(executable: str, minimum: tuple[int, int]) -> bool:
    probe = (
        "import sys; "
        f"raise SystemExit(0 if sys.version_info[:2] >= {minimum!r} else 1)"
    )
    return quiet([executable, "-c", probe])


def node_major(executable: str) -> int | None:
    try:
        result = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return None
    raw = result.stdout.strip().lstrip("v")
    try:
        return int(raw.split(".", 1)[0])
    except ValueError:
        return None


def find_tool(name: str, hint: str) -> str:
    path = shutil.which(name)
    if path is None:
        fail(f"{name} was not found. {hint}")
    return path


def ensure_cargo() -> str:
    cargo = shutil.which("cargo")
    if cargo is None:
        fail(
            "Rust/Cargo is required for the desktop app. Install it from "
            "https://rustup.rs and reopen the terminal."
        )
    return cargo


def ensure_node(*, dry_run: bool, no_install: bool) -> str:
    node = find_tool("node", "Install Node.js 18 or newer from https://nodejs.org.")
    npm = find_tool("npm", "Install npm together with Node.js from https://nodejs.org.")
    major = node_major(node)
    if major is None or major < MIN_NODE:
        fail(f"Node.js {MIN_NODE}+ is required (found {node}).")

    if not (FRONTEND / "node_modules" / "vite").is_dir():
        if no_install:
            fail("frontend/node_modules is missing. Run without --no-install first.")
        log("Installing frontend dependencies from the lockfile...")
        if not dry_run:
            run([npm, "ci", "--no-audit", "--no-fund"], cwd=FRONTEND)
        else:
            log("would run: npm ci --no-audit --no-fund")
    return npm


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def has_worker_dependencies(executable: str) -> bool:
    return quiet([executable, "-c", WORKER_IMPORTS])


def ensure_python(*, dry_run: bool, no_install: bool) -> str:
    """Prefer an existing ready interpreter; otherwise create .venv once."""
    explicit = os.environ.get("LODESTAR_PYTHON")
    if explicit:
        if not Path(explicit).exists():
            fail(f"LODESTAR_PYTHON points to a missing file: {explicit}")
        if not version_at_least(explicit, MIN_PYTHON):
            fail(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required at LODESTAR_PYTHON.")
        if not has_worker_dependencies(explicit):
            fail(
                "LODESTAR_PYTHON is set but its environment lacks the worker "
                "dependencies. Install them with: pip install -e ."
            )
        return explicit

    candidate = venv_python()
    if candidate.exists():
        if not version_at_least(str(candidate), MIN_PYTHON):
            fail(".venv uses an old Python. Remove .venv and run the launcher again.")
        if has_worker_dependencies(str(candidate)):
            return str(candidate)
        if no_install:
            fail(".venv is missing worker dependencies. Run without --no-install.")
        log("Installing the worker into .venv...")
        if dry_run:
            log("would run: .venv python -m pip install -e .")
            return str(candidate)
        run([str(candidate), "-m", "pip", "install", "-e", "."], cwd=ROOT, env=pip_env())
        if not has_worker_dependencies(str(candidate)):
            fail("worker dependencies are still missing after pip install.")
        return str(candidate)

    current = sys.executable
    if not version_at_least(current, MIN_PYTHON):
        found = sys.version.split()[0]
        fail(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required (found {found}).")
    if has_worker_dependencies(current):
        return current

    if no_install:
        fail(
            "No Python environment with the worker dependencies was found. "
            "Run without --no-install once to create .venv."
        )

    log("Creating .venv and installing the worker (one-time setup)...")
    if dry_run:
        log(f"would run: {sys.executable} -m venv .venv")
        log("would run: .venv python -m pip install -e .")
        return str(candidate)
    run([sys.executable, "-m", "venv", str(VENV)], cwd=ROOT)
    run([str(candidate), "-m", "pip", "install", "-e", "."], cwd=ROOT, env=pip_env())
    if not has_worker_dependencies(str(candidate)):
        fail("worker dependencies are missing after creating .venv.")
    return str(candidate)


def ensure_pyinstaller(python: str, *, dry_run: bool, no_install: bool) -> None:
    if quiet([python, "-m", "PyInstaller", "--version"]):
        return
    if no_install:
        fail('PyInstaller is missing. Run: python -m pip install -e ".[build]"')
    log("Installing PyInstaller for the worker sidecar...")
    if dry_run:
        log('would run: python -m pip install "pyinstaller>=6.11"')
        return
    run([python, "-m", "pip", "install", "pyinstaller>=6.11"], cwd=ROOT)


def migration_digest() -> str:
    digest = hashlib.sha256()
    if not MIGRATIONS.is_dir():
        return ""
    for path in sorted(MIGRATIONS.glob("*.sql")):
        digest.update(path.name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def remove_database(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        target = Path(f"{path}{suffix}")
        if target.exists():
            target.unlink()


def prepare_data_dir(raw: Path | None, *, reset: bool, dry_run: bool = False) -> Path:
    explicit = raw is not None
    data_dir = (raw or DEFAULT_DATA_DIR).expanduser().resolve()
    if dry_run:
        return data_dir
    database = data_dir / "lodestar.sqlite"
    marker = data_dir / ".schema-hash"
    digest = migration_digest()

    if reset:
        log(f"Resetting the dev database at {database}")
        remove_database(database)

    # Only the launcher's own data directory is disposable. A user-supplied
    # directory is never reset without --reset-data.
    if not explicit and database.exists() and marker.exists():
        previous = marker.read_text(encoding="utf-8").strip()
        if digest and previous != digest:
            log(f"Migration files changed; resetting the dev database at {database}")
            remove_database(database)

    if digest:
        data_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text(digest, encoding="utf-8")
    return data_dir


def doctor() -> int:
    add_cargo_to_path()
    problems: list[str] = []

    python = sys.executable
    candidate = venv_python()
    venv_ready = (
        candidate.exists()
        and version_at_least(str(candidate), MIN_PYTHON)
        and has_worker_dependencies(str(candidate))
    )
    worker_ready = venv_ready or (
        version_at_least(python, MIN_PYTHON) and has_worker_dependencies(python)
    )
    print(f"python       {python} ({sys.version.split()[0]})")
    print(f"  .venv      {'ready' if venv_ready else 'not created'}")
    print(f"  worker deps {'ready' if worker_ready else 'missing'}")

    node = shutil.which("node")
    npm = shutil.which("npm")
    major = node_major(node) if node else None
    print(f"node         {node or 'missing'}{f' ({major})' if major else ''}")
    print(f"npm          {npm or 'missing'}")
    frontend_state = (
        "installed"
        if (FRONTEND / "node_modules" / "vite").is_dir()
        else "needs npm ci"
    )
    print(f"frontend     {frontend_state}")
    print(f"cargo        {shutil.which('cargo') or 'missing'}")
    print(f"data dir     {DEFAULT_DATA_DIR}")

    if not worker_ready:
        problems.append("run `python scripts/run.py desktop` once to create .venv")
    if node is None or major is None or major < MIN_NODE:
        problems.append("install Node.js 18+")
    if npm is None:
        problems.append("install npm")
    if shutil.which("cargo") is None:
        problems.append("install Rust from https://rustup.rs for desktop/build")

    if problems:
        print("\nNeeds attention:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nAll prerequisites look ready.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run or build Job Signal on Windows, Linux, or macOS.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python scripts/run.py desktop\n"
            "  python scripts/run.py web --no-install\n"
            "  python scripts/run.py build\n"
            "  python scripts/run.py doctor\n"
        ),
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="desktop",
        choices=("desktop", "web", "build", "doctor"),
        help="what to do (default: desktop)",
    )
    parser.add_argument("--no-install", action="store_true", help="never install dependencies")
    parser.add_argument("--reset-data", action="store_true", help="delete the dev database first")
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="worker data directory (default: .lodestar-data)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show the commands without running them",
    )
    args = parser.parse_args()

    add_cargo_to_path()

    if args.mode == "doctor":
        return doctor()

    if args.mode in {"desktop", "build"}:
        ensure_cargo()

    npm = ensure_node(dry_run=args.dry_run, no_install=args.no_install)

    if args.mode == "web":
        log("Starting the browser preview at http://127.0.0.1:5173")
        command = [npm, "run", "dev", "--", "--host", "127.0.0.1"]
        if args.dry_run:
            log(format_command(command))
        else:
            run(command, cwd=FRONTEND)
        return 0

    env = os.environ.copy()
    if args.mode == "desktop":
        python = ensure_python(dry_run=args.dry_run, no_install=args.no_install)
        data_dir = prepare_data_dir(
            args.data_dir, reset=args.reset_data, dry_run=args.dry_run
        )
        env.update(
            {
                "LODESTAR_PYTHON": python,
                "LODESTAR_REPO_ROOT": str(ROOT),
                "LODESTAR_DATA_DIR": str(data_dir),
                "PYTHONUTF8": "1",
            }
        )
        log(f"Worker data: {data_dir}")
        command = [npm, "run", "tauri", "dev"]
        if args.dry_run:
            log(format_command(command))
            return 0
        run(command, cwd=FRONTEND, env=env)
        return 0

    if args.mode == "build":
        python = ensure_python(dry_run=args.dry_run, no_install=args.no_install)
        ensure_pyinstaller(python, dry_run=args.dry_run, no_install=args.no_install)
        worker_command = [python, "scripts/build_worker.py"]
        if args.dry_run:
            log(format_command(worker_command))
        else:
            run(worker_command, cwd=ROOT, env=env)

        command = [npm, "run", "tauri", "build"]
        if args.dry_run:
            log(format_command(command))
            return 0
        run(command, cwd=FRONTEND, env=env)
        log(f"Bundles are under {FRONTEND / 'src-tauri' / 'target' / 'release' / 'bundle'}")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
