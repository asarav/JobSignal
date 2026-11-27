"""Seed shards: ordering, checksums, bounded windows and the bootstrap list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.domain.services.classifier import classify_url
from app.plugins.discovery import SEEDS_DIR
from app.plugins.discovery.ats_directory import AtsDirectoryProvider
from app.plugins.discovery.seed_shards import (
    MANIFEST_NAME,
    SeedCatalog,
    SeedError,
    SeedRecord,
    read_manifest,
    records_from_legacy_rows,
    write_seed_shards,
)


def _record(connector: str, url: str, postings: int, tenant: str | None = None) -> SeedRecord:
    return SeedRecord(
        connector_id=connector,
        url=url,
        postings=postings,
        verified_at="2026-11-22",
        tenant=tenant,
        tenant_key=tenant,
    )


def test_shards_are_written_hiring_first_and_empty_last(tmp_path: Path) -> None:
    manifest = write_seed_shards(
        tmp_path,
        [
            _record("ats.greenhouse", "https://boards.greenhouse.io/a", 0),
            _record("ats.greenhouse", "https://boards.greenhouse.io/b", 40),
            _record("ats.greenhouse", "https://boards.greenhouse.io/c", 7),
        ],
        generated_at="2026-11-22T00:00:00Z",
    )

    assert manifest.total == 3
    assert manifest.hiring_total == 2
    catalog = SeedCatalog.open(tmp_path)
    assert [r.postings for r in catalog.records()] == [40, 7, 0]
    assert catalog.slice(0, 2)[0].url.endswith("/b")
    assert catalog.bootstrap(5)[-1].url.endswith("/c"), "bootstrap only returns hiring boards"


def test_seed_candidates_include_every_board_not_only_the_bootstrap(tmp_path: Path) -> None:
    write_seed_shards(
        tmp_path,
        [
            _record("ats.greenhouse", "https://boards.greenhouse.io/a", 0),
            _record("ats.greenhouse", "https://boards.greenhouse.io/b", 40),
        ],
        generated_at="2026-11-22T00:00:00Z",
    )
    provider = AtsDirectoryProvider.from_directory(tmp_path)

    candidates = list(provider.seed_candidates())
    assert [source.url for source in candidates] == [
        "https://boards.greenhouse.io/b",
        "https://boards.greenhouse.io/a",
    ]
    assert [source.observed_postings for source in candidates] == [40, 0]


def test_a_shard_is_verified_against_its_manifest_checksum(tmp_path: Path) -> None:
    write_seed_shards(tmp_path, [_record("ats.lever", "https://jobs.lever.co/acme", 3)])
    shard = tmp_path / "boards" / "ats-lever.ndjson"
    shard.write_text(
        json.dumps({"u": "https://jobs.lever.co/other", "p": 9}) + "\n", encoding="utf-8"
    )

    with pytest.raises(SeedError, match="checksum"):
        next(SeedCatalog.open(tmp_path).records())


def test_a_crlf_checkout_still_matches_its_checksum(tmp_path: Path) -> None:
    """Windows checkouts rewrite line endings; the manifest is over LF bytes."""
    write_seed_shards(tmp_path, [_record("ats.lever", "https://jobs.lever.co/acme", 3)])
    shard = tmp_path / "boards" / "ats-lever.ndjson"
    shard.write_bytes(shard.read_bytes().replace(b"\n", b"\r\n"))

    assert [r.url for r in SeedCatalog.open(tmp_path).records()] == [
        "https://jobs.lever.co/acme"
    ]


def test_a_stale_shard_is_removed_when_its_connector_disappears(tmp_path: Path) -> None:
    write_seed_shards(
        tmp_path,
        [
            _record("ats.lever", "https://jobs.lever.co/acme", 3),
            _record("ats.ashby", "https://jobs.ashbyhq.com/acme", 1),
        ],
    )
    write_seed_shards(tmp_path, [_record("ats.lever", "https://jobs.lever.co/acme", 3)])

    manifest = read_manifest(tmp_path)
    assert set(manifest.connectors) == {"ats.lever"}
    assert not (tmp_path / "boards" / "ats-ashby.ndjson").exists()


def test_the_window_wraps_and_never_exceeds_its_size(tmp_path: Path) -> None:
    write_seed_shards(
        tmp_path,
        [
            _record("ats.greenhouse", f"https://boards.greenhouse.io/t{n}", 10 - n)
            for n in range(10)
        ],
    )
    catalog = SeedCatalog.open(tmp_path)
    provider = AtsDirectoryProvider(catalog=catalog, window=4)

    first = catalog.slice(0, 4)
    assert [r.url[-2:] for r in first] == ["t0", "t1", "t2", "t3"]
    # The cursor advances by one step per pass and the window follows it.
    assert provider.window_start(0, 3, catalog.total) == 0
    assert provider.window_start(3, 3, catalog.total) == 4
    assert provider.window_start(9, 3, catalog.total) == 12 % 10
    assert len(catalog.slice(9, 4)) <= 4


def test_legacy_rows_keep_their_ranking_and_tier() -> None:
    records = records_from_legacy_rows(
        [
            {"url": "https://jobs.lever.co/a", "ats_hint": "ats.lever", "confidence": 0.9},
            {"url": "https://jobs.lever.co/b", "ats_hint": "ats.lever", "confidence": 0.6},
            {"url": "https://jobs.lever.co/c", "ats_hint": "ats.lever"},
        ],
        verified_at="2026-11-22",
    )

    assert [r.postings for r in records] == [1, 0, 1]
    assert records[0].verified_at == "2026-11-22"


def test_every_bundled_url_classifies_to_its_connector() -> None:
    """Nothing else catches the seed and the runtime classifier drifting apart."""
    catalog = SeedCatalog.open(SEEDS_DIR)

    mismatches = [
        (record.connector_id, classify_url(record.url)[0], record.url)
        for record in catalog.records()
        if classify_url(record.url)[0] != record.connector_id
    ]

    assert mismatches == []
    assert catalog.total > 30_000


def test_the_manifest_counts_match_the_shards() -> None:
    manifest = read_manifest(SEEDS_DIR)
    catalog = SeedCatalog.open(SEEDS_DIR)

    records = list(catalog.records())
    assert len(records) == manifest.total
    assert sum(1 for r in records if r.hiring) == manifest.hiring_total
    assert all(r.verified_at for r in records)


def test_the_bundled_seed_starts_with_the_largest_hiring_boards() -> None:
    catalog = SeedCatalog.open(SEEDS_DIR)
    top = catalog.bootstrap(50)

    assert len(top) == 50
    assert all(record.hiring for record in top)
    # Shards are ordered by how many hiring boards they hold, so the first
    # window comes from the biggest connector rather than an alphabetical one.
    assert top[0].connector_id == "ats.bamboohr"


def test_a_broken_manifest_is_reported(tmp_path: Path) -> None:
    (tmp_path / MANIFEST_NAME).write_text("{not json", encoding="utf-8")

    with pytest.raises(SeedError, match="not JSON"):
        read_manifest(tmp_path)
