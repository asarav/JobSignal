"""Unit tests for M5 resume parsing / keywords."""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.domain.models.resume import ResumeFileRef
from app.domain.services.keywords import KeywordExtractor, KeywordNormalizer
from app.domain.services.sectioner import section_text
from app.plugins.resume.txt import TxtResumeParser


def test_keyword_extraction():
    text = "We need a Python developer with React and AWS experience."
    skills = KeywordExtractor().extract(text)
    assert "python" in skills
    assert "react" in skills
    assert "aws" in skills


def test_keyword_normalizer_alias():
    n = KeywordNormalizer()
    assert n.normalize("reactjs") == "react"
    assert n.normalize("golang") == "go"


def test_sectioner():
    text = "Summary\nI am an engineer.\n\nSkills\nPython, SQL\n\nExperience\n- Built things"
    secs = section_text(text)
    assert "skills" in secs
    assert "Python" in secs["skills"] or "python" in secs["skills"].lower()


async def test_txt_parser():
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("Hello resume")
        path = f.name
    parser = TxtResumeParser()
    ref = ResumeFileRef(path=path, mime_type="text/plain", original_name="r.txt")
    res = await parser.parse(ref)
    from app.domain.models.common import Ok

    assert isinstance(res, Ok)
    assert "Hello resume" in res.value.text
    Path(path).unlink(missing_ok=True)
