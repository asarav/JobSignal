#!/usr/bin/env python3
"""Check documentation consistency: links, ADR references, milestone coverage.

Run from the repository root: ``python scripts/check_docs.py``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LINK = re.compile(r"\[[^\]]*]\(([^)]+)\)")
ADR = re.compile(r"docs/adr/(\d{4}-[a-z0-9-]+\.md)")


def markdown_files() -> list[Path]:
    return sorted([ROOT / "README.md", *ROOT.glob("docs/**/*.md")])


def check_links(files: list[Path]) -> list[str]:
    problems: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for raw in LINK.findall(text):
            target = raw.strip().split(" ", 1)[0]
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target = target.split("#", 1)[0]
            if not target:
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                problems.append(f"{path.relative_to(ROOT)}: broken link -> {raw}")
    return problems


def check_adrs(files: list[Path]) -> list[str]:
    problems: list[str] = []
    referenced: set[str] = set()
    for path in files:
        referenced.update(ADR.findall(path.read_text(encoding="utf-8")))
    for name in sorted(referenced):
        if not (ROOT / "docs" / "adr" / name).exists():
            problems.append(f"referenced ADR missing: docs/adr/{name}")
    return problems


def check_milestones(files: list[Path]) -> list[str]:
    corpus = "\n".join(path.read_text(encoding="utf-8") for path in files)
    problems = []
    for milestone in range(12):
        token = f"M{milestone}"
        if not re.search(rf"\b{token}\b", corpus):
            problems.append(f"milestone {token} is not referenced by README or docs")
    return problems


def main() -> int:
    files = markdown_files()
    problems = [*check_links(files), *check_adrs(files), *check_milestones(files)]
    if problems:
        print("documentation check failed:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"documentation check passed ({len(files)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
