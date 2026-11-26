"""Bundled seed shards (v2): one compact NDJSON file per ATS plus a manifest.

The seed used to be a single ``ats_tenants.json`` array written with
``indent=2``: 35k objects, ~6.7 MB, re-parsed and re-emitted in full on every
discovery pass.  It is a bootstrap, not the engine, so it is stored the way a
bootstrap is actually consumed:

* ``seeds/manifest.json`` names the format version, when it was generated and
  one entry per connector with the shard file, its board/hiring counts and a
  SHA-256 of the exact bytes;
* ``seeds/boards/<connector>.ndjson`` holds one compact record per line,
  largest-first by observed postings::

      {"u": "https://boards.greenhouse.io/stripe", "p": 412,
       "v": "2026-11-22", "t": "stripe"}

  ``p`` is the posting count observed at build time, ``v`` the date it was
  observed, ``t`` the tenant and ``k`` the ATS tenant key when the URL alone
  does not carry it (Workday's site, Oracle's site code, UKG's board id).

Live-but-empty boards (``p == 0``) stay in the shard and sort last: they cost
bytes, not requests, and a quiet employer is future supply.  The loader hands
out bounded windows so a pass proposes a few hundred boards instead of
materialising all 35k on every run, and ``bootstrap`` returns the largest
hiring boards for a cold start.

The format is intentionally plain (no compression, no database): it stays
reviewable in a diff, and ``python scripts/build_seed.py`` rewrites one shard
per connector instead of the whole file.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Format version written into the manifest.  A loader that does not know this
#: version refuses the directory instead of guessing.
SCHEMA_VERSION = 2

MANIFEST_NAME = "manifest.json"
BOARDS_DIR = "boards"

#: Short record keys, documented in the manifest's ``fields`` block.
FIELDS: dict[str, str] = {
    "u": "board url",
    "p": "postings observed at build time",
    "v": "date the board was verified (YYYY-MM-DD)",
    "t": "ats tenant",
    "k": "ats tenant key when the url does not carry it",
}


class SeedError(RuntimeError):
    """The seed directory is missing, malformed or failed its checksum."""


@dataclass(frozen=True, slots=True)
class SeedRecord:
    connector_id: str
    url: str
    postings: int = 0
    verified_at: str = ""
    tenant: str | None = None
    tenant_key: str | None = None

    @property
    def hiring(self) -> bool:
        return self.postings > 0

    @property
    def company_hint(self) -> str | None:
        return _hint(self.tenant)

    def as_seed_dict(self) -> dict[str, object]:
        """The legacy row shape, for callers that still consume dicts."""
        row: dict[str, object] = {
            "url": self.url,
            "company_hint": self.company_hint,
            "ats_hint": self.connector_id,
            "confidence": 0.9 if self.hiring else 0.6,
        }
        if self.tenant_key:
            row["tenant_key"] = self.tenant_key
        return row

    def as_line(self) -> str:
        record: dict[str, object] = {"u": self.url, "p": self.postings}
        if self.verified_at:
            record["v"] = self.verified_at
        if self.tenant:
            record["t"] = self.tenant
        if self.tenant_key:
            record["k"] = self.tenant_key
        return json.dumps(record, ensure_ascii=False, separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ShardInfo:
    connector_id: str
    file: str
    boards: int
    hiring: int
    sha256: str


@dataclass(frozen=True, slots=True)
class SeedManifest:
    version: int
    generated_at: str
    connectors: Mapping[str, ShardInfo]
    #: Connectors whose public listing renders client-side, so a zero there is
    #: "not observed" rather than "no openings".
    zero_unconfident: frozenset[str] = frozenset()

    @property
    def total(self) -> int:
        return sum(info.boards for info in self.connectors.values())

    @property
    def hiring_total(self) -> int:
        return sum(info.hiring for info in self.connectors.values())

    def ordered(self) -> tuple[ShardInfo, ...]:
        """Shards by how many hiring boards they hold, then connector id.

        Ordering is the ranking: the first windows a pass hands out are the
        biggest hiring boards across every ATS, which is what a cold start
        (and the bootstrap) needs.
        """
        return tuple(
            sorted(self.connectors.values(), key=lambda info: (-info.hiring, info.connector_id))
        )


# --------------------------------------------------------------------- writing


def write_seed_shards(
    directory: Path,
    records: Iterable[SeedRecord],
    *,
    generated_at: str | None = None,
    zero_unconfident: Iterable[str] = (),
) -> SeedManifest:
    """Write one NDJSON shard per connector plus the manifest.

    Records are grouped by connector and sorted largest-first (ties keep their
    input order, so a caller that already ranked them keeps that order).
    Shard files are rewritten atomically and stale shards for connectors that
    no longer exist are removed.
    """
    grouped: dict[str, list[SeedRecord]] = {}
    for record in records:
        if not record.url or not record.connector_id:
            continue
        grouped.setdefault(record.connector_id, []).append(record)

    boards_dir = directory / BOARDS_DIR
    boards_dir.mkdir(parents=True, exist_ok=True)
    stamp = generated_at or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    connectors: dict[str, ShardInfo] = {}
    for connector_id in sorted(grouped):
        rows = sorted(
            enumerate(grouped[connector_id]), key=lambda item: (-item[1].postings, item[0])
        )
        lines = [record.as_line() for _index, record in rows]
        body = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
        name = f"{BOARDS_DIR}/{_safe_name(connector_id)}.ndjson"
        _atomic_write(directory / name, body)
        connectors[connector_id] = ShardInfo(
            connector_id=connector_id,
            file=name,
            boards=len(lines),
            hiring=sum(1 for _index, record in rows if record.hiring),
            sha256=hashlib.sha256(body).hexdigest(),
        )

    # A connector with no boards left must not leave its old shard behind.
    for stale in boards_dir.glob("*.ndjson"):
        relative = stale.relative_to(directory).as_posix()
        if all(info.file != relative for info in connectors.values()):
            stale.unlink()

    manifest = SeedManifest(
        version=SCHEMA_VERSION,
        generated_at=stamp,
        connectors=connectors,
        zero_unconfident=frozenset(zero_unconfident),
    )
    payload = {
        "version": manifest.version,
        "generated_at": manifest.generated_at,
        "ordering": "hiring_first",
        "fields": FIELDS,
        "zero_unconfident_connectors": sorted(manifest.zero_unconfident),
        "totals": {"boards": manifest.total, "hiring": manifest.hiring_total},
        "connectors": {
            connector_id: {
                "file": info.file,
                "boards": info.boards,
                "hiring": info.hiring,
                "with_postings": info.hiring,
                "sha256": info.sha256,
            }
            for connector_id, info in sorted(
                connectors.items(),
                key=lambda item: (-item[1].hiring, item[0]),
            )
        },
    }
    _atomic_write(
        directory / MANIFEST_NAME,
        (json.dumps(payload, indent=2) + "\n").encode("utf-8"),
    )
    return manifest


def _safe_name(connector_id: str) -> str:
    return connector_id.replace(".", "-")


def _atomic_write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(body)
    temporary.replace(path)


# --------------------------------------------------------------------- reading


def read_manifest(directory: Path) -> SeedManifest:
    path = directory / MANIFEST_NAME
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SeedError(f"seed manifest is unreadable: {path}") from exc
    except ValueError as exc:
        raise SeedError(f"seed manifest is not JSON: {path}") from exc
    if not isinstance(raw, dict):
        raise SeedError(f"seed manifest is not an object: {path}")
    version = raw.get("version")
    if version != SCHEMA_VERSION:
        raise SeedError(
            f"seed manifest version {version!r} is not supported (want {SCHEMA_VERSION})"
        )
    connectors: dict[str, ShardInfo] = {}
    entries = raw.get("connectors")
    if not isinstance(entries, dict):
        raise SeedError("seed manifest has no connectors block")
    for connector_id, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        file = entry.get("file")
        boards = entry.get("boards")
        hiring = entry.get("hiring", entry.get("with_postings", 0))
        checksum = entry.get("sha256")
        if not isinstance(file, str) or not isinstance(boards, int):
            continue
        connectors[str(connector_id)] = ShardInfo(
            connector_id=str(connector_id),
            file=file,
            boards=boards,
            hiring=hiring if isinstance(hiring, int) else 0,
            sha256=checksum if isinstance(checksum, str) else "",
        )
    unconfident = raw.get("zero_unconfident_connectors")
    return SeedManifest(
        version=version,
        generated_at=str(raw.get("generated_at") or ""),
        connectors=connectors,
        zero_unconfident=frozenset(
            str(item) for item in unconfident if isinstance(item, str)
        )
        if isinstance(unconfident, list)
        else frozenset(),
    )


def read_shard(directory: Path, info: ShardInfo) -> list[SeedRecord]:
    """One shard's records, with its manifest checksum verified."""
    path = directory / info.file
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise SeedError(f"seed shard is unreadable: {info.file}") from exc
    # A checkout that rewrites line endings (``core.autocrlf`` on Windows) must
    # not read as tampering: the manifest checksum is over LF bytes.
    body = body.replace(b"\r\n", b"\n")
    if info.sha256:
        digest = hashlib.sha256(body).hexdigest()
        if digest != info.sha256:
            raise SeedError(
                f"seed shard checksum does not match the manifest: {info.file}"
            )
    records: list[SeedRecord] = []
    for number, line in enumerate(body.decode("utf-8", errors="replace").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            raw = json.loads(line)
        except ValueError as exc:
            raise SeedError(f"{info.file}:{number} is not JSON") from exc
        if not isinstance(raw, dict) or not isinstance(raw.get("u"), str):
            raise SeedError(f"{info.file}:{number} has no url")
        postings = raw.get("p", 0)
        records.append(
            SeedRecord(
                connector_id=info.connector_id,
                url=str(raw["u"]),
                postings=int(postings) if isinstance(postings, (int, float)) else 0,
                verified_at=str(raw.get("v") or ""),
                tenant=str(raw["t"]) if isinstance(raw.get("t"), str) else None,
                tenant_key=str(raw["k"]) if isinstance(raw.get("k"), str) else None,
            )
        )
    return records


def read_all_records(directory: Path) -> list[SeedRecord]:
    """Every record in the directory, in shard order (hiring shards first)."""
    manifest = read_manifest(directory)
    return [record for info in manifest.ordered() for record in read_shard(directory, info)]


# --------------------------------------------------------------------- catalog


class SeedCatalog:
    """Read-only, lazily-read view of the seed shards.

    ``slice(offset, limit)`` is the pass-sized window the directory provider
    yields; it reads only the shards that window touches.  ``bootstrap(limit)``
    is the cold-start list: largest hiring boards first, never empty ones.
    """

    def __init__(self, directory: Path, manifest: SeedManifest | None = None) -> None:
        self.directory = directory
        self.manifest = manifest or read_manifest(directory)

    @classmethod
    def open(cls, directory: Path) -> SeedCatalog:
        return cls(directory)

    @property
    def total(self) -> int:
        return self.manifest.total

    @property
    def hiring_total(self) -> int:
        return self.manifest.hiring_total

    def connector_counts(self) -> dict[str, int]:
        return {
            connector_id: info.boards
            for connector_id, info in sorted(self.manifest.connectors.items())
        }

    def records(self) -> Iterator[SeedRecord]:
        for info in self.manifest.ordered():
            yield from read_shard(self.directory, info)

    def slice(self, offset: int, limit: int) -> list[SeedRecord]:
        """A bounded window of the ordered seed, wrapping at the end."""
        total = self.total
        if total <= 0 or limit <= 0:
            return []
        position = offset % total
        wanted = min(limit, total)
        window: list[SeedRecord] = []
        for info in self.manifest.ordered():
            if position >= info.boards:
                position -= info.boards
                continue
            for index, record in enumerate(read_shard(self.directory, info)):
                if index < position:
                    continue
                window.append(record)
                if len(window) >= wanted:
                    return window
            position = 0
        return window

    def bootstrap(self, limit: int) -> list[SeedRecord]:
        """The largest hiring boards, for a cold start."""
        if limit <= 0:
            return []
        found: list[SeedRecord] = []
        for info in self.manifest.ordered():
            if not info.hiring:
                continue
            for record in read_shard(self.directory, info):
                if not record.hiring:
                    break  # shards are hiring-first
                found.append(record)
                if len(found) >= limit:
                    return found
        return found


# ---------------------------------------------------------------------- legacy


def records_from_legacy_rows(
    rows: Sequence[Mapping[str, Any]], *, verified_at: str = ""
) -> list[SeedRecord]:
    """Convert the old ``ats_tenants.json`` rows into shard records.

    The old file never carried a posting count, only a confidence tier, so a
    hiring board becomes ``p=1`` and an empty one ``p=0``.  Input order is
    preserved, so the file's largest-first ranking survives the ``p`` tie.
    """
    records: list[SeedRecord] = []
    for row in rows:
        url = row.get("url")
        connector = row.get("ats_hint")
        if not isinstance(url, str) or not url:
            continue
        confidence = row.get("confidence", 0.9)
        hiring = (
            isinstance(confidence, (int, float))
            and not isinstance(confidence, bool)
            and confidence >= 0.9
        )
        tenant = row.get("tenant_hint") or row.get("company_hint")
        records.append(
            SeedRecord(
                connector_id=connector if isinstance(connector, str) else "",
                url=url,
                postings=1 if hiring else 0,
                verified_at=verified_at,
                tenant=str(tenant) if isinstance(tenant, str) else None,
                tenant_key=(
                    str(row["tenant_key"])
                    if isinstance(row.get("tenant_key"), str) and row["tenant_key"]
                    else None
                ),
            )
        )
    return records


def _hint(tenant: str | None) -> str | None:
    """A readable label from an ATS tenant slug."""
    if not tenant:
        return None
    name = tenant
    for prefix in ("careers-", "jobs-", "staff-"):
        if name.startswith(prefix) and len(name) > len(prefix):
            name = name[len(prefix) :]
            break
    return name.replace("-", " ").title()
