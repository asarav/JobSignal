"""Safety: Milestone 0 ships no crawling, ATS, resume, matching, or browser code.

These are scope guards, not feature tests. They fail if a later milestone's
production code, dependency, IPC method, or database table leaks into M0.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "app"

FORBIDDEN_DEPENDENCIES = (
    "playwright",
    "selenium",
    "scrapy",
    "beautifulsoup4",
    "lxml",
    "selectolax",
    "pymupdf",
    "python-docx",
    "scikit-learn",
    "onnxruntime",
    "sentence-transformers",
)

# Module-level imports that would mean real crawling/browsing/parsing landed.
FORBIDDEN_IMPORTS = (
    "playwright",
    "selenium",
    "scrapy",
    "bs4",
    "lxml",
    "selectolax",
    "fitz",
    "docx",
    "sklearn",
    "onnxruntime",
)

ALLOWED_TABLES = {
    "schema_migrations",
    "app_settings",
    "plugin_registry",
    "plugin_config",
    "background_tasks",
    "sources",
    "companies",
    "jobs",
    "job_aliases",
    "job_snapshots",
    "job_observations",
    "crawl_runs",
    "source_health",
    "jobs_fts",
    "jobs_fts_config",
    "jobs_fts_data",
    "jobs_fts_docsize",
    "jobs_fts_idx",
    "sqlite_sequence",
    "sqlite_stat1",
}


def _production_files() -> list[Path]:
    return [
        path
        for path in APP.rglob("*.py")
        if "__pycache__" not in path.parts
    ]


@pytest.mark.parametrize("dependency", FORBIDDEN_DEPENDENCIES)
def test_pyproject_has_no_later_milestone_dependency(dependency):
    text = (ROOT / "pyproject.toml").read_text("utf-8").lower()
    assert dependency not in text, f"{dependency} belongs to a later milestone"


@pytest.mark.parametrize("module", FORBIDDEN_IMPORTS)
def test_production_code_imports_no_browser_or_parser_library(module):
    pattern = re.compile(rf"^\s*(?:from|import)\s+{re.escape(module)}\b", re.MULTILINE)
    offenders = [
        str(path.relative_to(ROOT))
        for path in _production_files()
        if pattern.search(path.read_text("utf-8"))
    ]
    assert offenders == [], f"{module} is imported by {offenders}"


def test_no_form_submission_code():
    offenders = []
    for path in _production_files():
        text = path.read_text("utf-8").lower()
        if any(token in text for token in ("submit_application", "click_submit", "autofill(")):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == [], f"application submission code found in {offenders}"


async def test_migration_0001_creates_only_infrastructure_tables(tmp_path):
    from app.infrastructure.persistence.sqlite.migrator import migrate

    db = tmp_path / "scope.sqlite"
    await migrate(db)

    import aiosqlite

    async with aiosqlite.connect(db) as connection:
        cursor = await connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
        tables = {row[0] for row in await cursor.fetchall()}

    assert tables == ALLOWED_TABLES - {"sqlite_sequence", "sqlite_stat1"}


def test_no_forbidden_ipc_methods_are_registered():
    from app.worker.ipc.method_catalog import FORBIDDEN_METHOD_PREFIXES, M0_METHOD_NAMES

    for method in M0_METHOD_NAMES:
        assert not any(method.startswith(prefix) for prefix in FORBIDDEN_METHOD_PREFIXES), method
