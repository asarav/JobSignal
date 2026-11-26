"""HTML/XSS safety: untrusted posting text is never injected as markup.

The app does not render posting HTML at all. This test pins that control: no
component uses raw-HTML APIs, and the safety corpus stays available for the
rendering test.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend" / "src"
CORPUS = ROOT / "tests" / "fixtures" / "xss" / "payloads.json"

RAW_HTML_API = ("dangerouslySetInnerHTML", "innerHTML =", "outerHTML =", "document.write(")


def _frontend_sources() -> list[Path]:
    return [
        path
        for path in sorted(FRONTEND.rglob("*"))
        if path.suffix in {".ts", ".tsx"} and ".test." not in path.name
    ]


def test_no_component_uses_raw_html_apis() -> None:
    hits: list[str] = []
    for path in _frontend_sources():
        source = path.read_text(encoding="utf-8")
        # Comments may *name* the API they forbid; code may not call it.
        code_lines = [
            line
            for line in source.splitlines()
            if not line.strip().startswith(("//", "*", "/*", "{/*"))
        ]
        code = "\n".join(code_lines)
        for api in RAW_HTML_API:
            if api in code:
                hits.append(f"{path.relative_to(ROOT)}: {api}")
    assert hits == [], "\n".join(hits)


def test_job_text_is_rendered_by_the_text_only_component() -> None:
    route = (FRONTEND / "features" / "jobs" / "JobDetailRoute.tsx").read_text(
        encoding="utf-8"
    )
    assert "JobDescription" in route
    assert "dangerouslySetInnerHTML" not in route


def test_the_xss_corpus_covers_the_common_vectors() -> None:
    payloads = json.loads(CORPUS.read_text(encoding="utf-8"))["payloads"]
    joined = "\n".join(payloads)
    for marker in ("<script", "onerror", "javascript:", "<iframe", "onload"):
        assert marker in joined, marker
