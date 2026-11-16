from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ResumeFileRef:
    path: str
    mime_type: str
    original_name: str
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class ParsedResume:
    text: str
    sections: Sequence[str] = ()
    parser_id: str = ""
    parser_version: str = ""


@dataclass(frozen=True, slots=True)
class KeywordContext:
    kind: str
    source_id: str | None = None


@dataclass(frozen=True, slots=True)
class KeywordMention:
    raw: str
    context: str | None = None
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True, slots=True)
class NormalizedKeyword:
    id: str
    canonical: str
    category: str
    taxonomy_version: str
