"""Config merge, immutability, and §20 defaults."""

import pytest
from app.application.config import AppConfig, deep_merge, validate_patch
from pydantic import ValidationError


def test_defaults():
    cfg = AppConfig()
    assert cfg.app_version == "0.0.0"
    assert cfg.protocol_version == 1
    assert cfg.features.search_engine_discovery is False
    assert cfg.features.external_plugins is False
    assert cfg.features.autofill is True
    assert cfg.http.max_bytes == 5_000_000
    assert cfg.crawl.global_concurrency == 8
    assert cfg.extraction.order[0] == "extract.public_api"
    assert cfg.matching.model == "fit.weighted_v1"
    assert cfg.posting_intelligence.model == "risk.heuristic_v1"


def test_with_data_dir():
    cfg = AppConfig().with_data_dir("/tmp/x")
    assert cfg.paths.data_dir == "/tmp/x"


def test_deep_merge_preserves_unrelated_keys():
    merged = deep_merge(
        {"http": {"timeout_s": 20, "max_bytes": 10}, "features": {"autofill": True}},
        {"http": {"timeout_s": 5}},
    )
    assert merged["http"] == {"timeout_s": 5, "max_bytes": 10}
    assert merged["features"] == {"autofill": True}


def test_apply_patch_merges_nested_sections():
    cfg = AppConfig().apply_patch({"logging": {"level": "debug"}})
    assert cfg.logging.level == "debug"
    assert cfg.logging.rotate_files == 10


def test_apply_patch_ignores_nothing_silently():
    with pytest.raises(ValidationError):
        AppConfig().apply_patch({"http": {"unknown_field": 1}})


def test_immutable_paths_cannot_be_patched():
    for patch in (
        {"paths": {"data_dir": "/elsewhere"}},
        {"paths": {"db_name": "other.sqlite"}},
        {"app_version": "9.9.9"},
        {"protocol_version": 2},
    ):
        with pytest.raises(ValueError):
            AppConfig().apply_patch(patch)


def test_validate_patch_returns_copy():
    patch = {"logging": {"level": "warning"}}
    returned = validate_patch(patch)
    assert returned == patch
    assert returned is not patch
