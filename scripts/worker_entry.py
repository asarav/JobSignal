"""Entry point used by the PyInstaller worker sidecar."""

from __future__ import annotations

from app.worker.main import run

if __name__ == "__main__":
    run()
