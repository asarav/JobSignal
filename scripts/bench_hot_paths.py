"""Benchmark the hot read paths against a checked-in baseline (M11).

Usage:
    python scripts/bench_hot_paths.py                # compare with the baseline
    python scripts/bench_hot_paths.py --write        # refresh the baseline
    python scripts/bench_hot_paths.py --repeats 5

The dataset is deterministic: 20 companies, 500 postings, 100 tracked
applications and a keyword revision per posting, all in a temporary SQLite
database built through the real composition root.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.application.benchmarks import (  # noqa: E402
    BASELINE_SLACK_MS,
    run_scenarios,
    summarize,
)
from app.application.commands.applications import ApplicationsUseCase  # noqa: E402
from app.application.config import AppConfig  # noqa: E402
from app.application.dto import (  # noqa: E402
    ApplicationCreateParams,
    ApplicationOutcomeParams,
)
from app.application.keywords import KeywordPersistenceService  # noqa: E402
from app.worker.composition_root import build_container  # noqa: E402
from tests.support.fakes import FakeClock, FakeIdGenerator  # noqa: E402

BASELINE = ROOT / "benchmarks" / "hot_paths_baseline.json"
NOW = datetime(2026, 3, 1, tzinfo=UTC)


async def _build(data_dir: Path):
    clock = FakeClock(NOW)
    cfg = AppConfig().with_data_dir(str(data_dir))
    return build_container(
        cfg,
        overrides={"clock": clock, "ids": FakeIdGenerator()},
        log_dir=None,
    )


async def _seed(container) -> None:
    await container.uow_factory.initialize()
    db = container.uow_factory.db_path
    stamp = NOW.isoformat()
    async with aiosqlite.connect(db) as conn:
        await conn.executemany(
            "INSERT INTO companies(id, canonical_name, normalized_name, created_at) "
            "VALUES (?, ?, ?, ?)",
            [(f"co_{index}", f"Company {index}", f"company {index}", stamp) for index in range(20)],
        )
        await conn.execute(
            "INSERT INTO sources(id, url, kind, created_at, updated_at) "
            "VALUES ('src_1', 'https://acme.test/careers', 'ats', ?, ?)",
            (stamp, stamp),
        )
        rows = []
        for index in range(500):
            posted = (NOW - timedelta(days=index % 40)).isoformat()
            rows.append(
                (
                    f"job_{index}",
                    f"co_{index % 20}",
                    "Senior Data Engineer",
                    "senior data engineer",
                    "Own ingestion pipelines with python and sql. "
                    "Work with kubernetes and airflow.",
                    "src_1",
                    f"https://acme.test/jobs/{index}",
                    "active",
                    f"hash-{index}",
                    posted,
                    posted,
                    stamp,
                    stamp,
                    stamp,
                    stamp,
                )
            )
        await conn.executemany(
            "INSERT INTO jobs(id, company_id, title, normalized_title, description_text, "
            "source_id, canonical_url, posting_status, content_hash, posted_at, "
            "first_seen_at, last_seen_at, fetched_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        await conn.commit()

    persistence = KeywordPersistenceService(container)
    async with container.uow_factory() as uow:
        for index in range(500):
            job = await uow.jobs.get(f"job_{index}")
            if job is not None:
                await persistence.persist_for_job(uow, job, NOW)
        await uow.commit()

    uses = ApplicationsUseCase(container)
    for index in range(100):
        created = await uses.create(ApplicationCreateParams(job_id=f"job_{index}"))
        if index % 2 == 0:
            await uses.record_outcome(
                ApplicationOutcomeParams(
                    application_id=created.application_id,
                    kind="interview" if index % 4 == 0 else "rejection",
                )
            )


def _load_baseline() -> dict:
    if not BASELINE.exists():
        return {}
    return json.loads(BASELINE.read_text(encoding="utf-8"))


async def main(args: argparse.Namespace) -> int:
    with tempfile.TemporaryDirectory(prefix="jobsignal-bench-") as tmp:
        container = await _build(Path(tmp))
        await _seed(container)
        baseline = None if args.write else _load_baseline()
        results = await run_scenarios(container, repeats=args.repeats, baseline=baseline)
        summary = summarize(results)
    print(json.dumps(summary, indent=2))
    if args.write:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(
            json.dumps(
                {
                    "dataset": {
                        "companies": 20,
                        "jobs": 500,
                        "applications": 100,
                        "keyword_revisions": 500,
                    },
                    "tolerance": 1.25,
                    "absolute_slack_ms": BASELINE_SLACK_MS,
                    "scenarios": {
                        name: {"median_ms": entry["median_ms"]}
                        for name, entry in summary["scenarios"].items()
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"baseline written to {BASELINE.relative_to(ROOT)}")
        return 0
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--repeats", type=int, default=3)
    raise SystemExit(asyncio.run(main(parser.parse_args())))
