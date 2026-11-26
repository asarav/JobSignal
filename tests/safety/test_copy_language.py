"""M8/M9 copy lint: cautious, non-causal user-facing language.

The spec forbids calling a posting or company a scam/fake/deceptive thing and
forbids claiming that a signal *proved* or *caused* an outcome. This scans
user-facing strings, not documentation, docstrings, SQL or test fixtures.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"
APP = ROOT / "app"

FORBIDDEN = re.compile(
    r"\b(scam|scams|fake|fakes|faked|deceptive|proves|proven|caused)\b",
    re.IGNORECASE,
)
FORBIDDEN_PHRASES = ("confirms ghost", "confirms a ghost")


def find_hits(text: str) -> list[str]:
    hits = [match.group(0) for match in FORBIDDEN.finditer(text)]
    lowered = text.casefold()
    hits.extend(phrase for phrase in FORBIDDEN_PHRASES if phrase in lowered)
    return hits


def _is_identifier_like(value: str) -> bool:
    """Skip event names, method names and keys (``browser.fake``)."""
    return " " not in value and any(separator in value for separator in "._/:")


def frontend_strings() -> list[tuple[str, str]]:
    strings: list[tuple[str, str]] = []
    for path in sorted(FRONTEND.rglob("*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        if ".test." in path.name:
            continue
        strings.append((str(path.relative_to(ROOT)), path.read_text(encoding="utf-8")))
    return strings


def backend_strings() -> list[tuple[str, str]]:
    """Every non-docstring string constant in production Python."""
    strings: list[tuple[str, str]] = []
    for path in sorted(APP.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                body = getattr(node, "body", [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                strings.append((str(path.relative_to(ROOT)), node.value))
    return strings


def test_shipped_copy_avoids_forbidden_language() -> None:
    hits: list[str] = []
    for path, text in [*frontend_strings(), *backend_strings()]:
        if _is_identifier_like(text):
            continue
        for hit in find_hits(text):
            hits.append(f"{path}: {hit!r} in {text[:120]!r}")
    assert hits == [], "\n".join(hits)


def test_the_scanner_detects_planted_causal_language() -> None:
    assert find_hits("This proves the posting is a scam.")
    assert find_hits("The gap caused fewer replies.")
    assert find_hits("Confirms ghost behaviour.")
    assert find_hits("A perfectly cautious sentence.") == []
