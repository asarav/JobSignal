"""ADR-0012: auto-update stays off, and the build cannot replace itself."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAURI = ROOT / "frontend" / "src-tauri"


def test_the_tauri_config_has_no_updater() -> None:
    config = json.loads((TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    text = json.dumps(config)
    assert "updater" not in text
    assert "plugins" not in config
    assert not config.get("bundle", {}).get("createUpdaterArtifacts", False)
    assert config["bundle"].get("targets")


def test_no_updater_plugin_is_linked_or_registered() -> None:
    """Removing the config alone would not be enough: the Rust side must agree."""

    cargo = (TAURI / "Cargo.toml").read_text(encoding="utf-8")
    assert "tauri-plugin-updater" not in cargo
    lib = (TAURI / "src" / "lib.rs").read_text(encoding="utf-8")
    assert "updater" not in lib.casefold()


def test_the_adr_records_the_decision_and_the_rollback_rule() -> None:
    adr = (ROOT / "docs" / "adr" / "0012-auto-update.md").read_text(encoding="utf-8")
    assert adr.startswith("# ADR-0012")
    assert "Auto-update ships **disabled**" in adr
    assert "signed manifest" in adr
    assert "manual action" in adr
