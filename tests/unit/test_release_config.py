"""Release configuration stays verifiable without a macOS machine."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_bundle_targets_cover_every_installer_kind() -> None:
    config = json.loads((ROOT / "frontend/src-tauri/tauri.conf.json").read_text())
    targets = config["bundle"]["targets"]
    kinds = {"msi", "nsis", "dmg", "appimage", "deb"}
    # ``all`` asks Tauri for every bundle its host supports.
    selected = kinds if targets == "all" else set(targets)
    for target in kinds:
        assert target in selected, f"{target} is missing from bundle.targets"


def test_the_bundled_worker_is_the_only_external_binary() -> None:
    config = json.loads((ROOT / "frontend/src-tauri/tauri.conf.json").read_text())
    assert config["bundle"]["externalBin"] == ["binaries/lodestar-worker"]
    assert "updater" not in config.get("plugins", {})
