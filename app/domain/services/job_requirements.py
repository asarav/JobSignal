"""Section-aware job requirements and ATS keyword extraction.

A posting's skills list is not a requirements list.  "Python" in a
responsibilities paragraph, "React is a plus" in a nice-to-have section and
"must have 5 years of Go" are three different signals.  This analyzer keeps
them apart so matching can gate on real required skills instead of treating
every mentioned technology as mandatory.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser
from typing import Any

from app.domain.services.keywords import KeywordExtractor, KeywordNormalizer
from app.domain.services.location_scope import parse_location_scope

REQUIREMENTS_VERSION = "requirements.v3"

_REQUIRED_HEADINGS = (
    "requirements",
    "required",
    "required qualifications",
    "minimum qualifications",
    "basic qualifications",
    "must have",
    "must-have",
    "must haves",
    "what you'll need",
    "what you will need",
    "what you need",
    "what we're looking for",
    "what we are looking for",
    "who you are",
    "what you bring",
    "about you",
    "you have",
    "qualifications",
    "skills",
    "experience",
)
_PREFERRED_HEADINGS = (
    "nice to have",
    "nice-to-have",
    "nice to haves",
    "preferred",
    "preferred qualifications",
    "preferred skills",
    "bonus",
    "bonus points",
    "bonus if you have",
    "bonus if you",
    "a plus",
    "plus",
    "pluses",
    "desired",
    "good to have",
    "good-to-have",
    "it's a bonus",
)
_OTHER_HEADINGS = (
    "responsibilities",
    "what you'll do",
    "what you will do",
    "the role",
    "about the role",
    "benefits",
    "perks",
    "why join",
    "about us",
    "about the team",
    "compensation",
    "how to apply",
)
_REQUIRED_CUE = re.compile(
    r"\b(must(?:\s+have)?|required|requirements?|proficien(?:t|cy)|"
    r"expert(?:ise)?\s+(?:in|with)|experience\s+(?:with|in)|knowledge\s+of|"
    r"familiar(?:ity)?\s+with|hands[- ]on|strong\s+(?:skills?|background)\s+(?:in|with)|"
    r"ability\s+to|you\s+have|we\s+require)\b",
    re.IGNORECASE,
)
_PREFERRED_CUE = re.compile(
    r"\b(preferred|nice\s+to\s+have|bonus|a\s+plus|pluses?|desired|"
    r"good\s+to\s+have|advantage(?:ous)?|ideally)\b",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^[#*\s]*(?P<heading>[A-Za-z][A-Za-z &'/’\-]{2,48})\s*:?\s*$")
_BULLET_RE = re.compile(r"^\s*(?:[-*•·▪◦]|\d+[.)])\s*")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+|\s*[•·]\s*")


@dataclass(frozen=True, slots=True)
class AtsKeyword:
    keyword_id: str
    canonical: str
    phrase: str
    importance: str
    frequency: int
    section: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "keyword_id": self.keyword_id,
            "canonical": self.canonical,
            "phrase": self.phrase,
            "importance": self.importance,
            "frequency": self.frequency,
            "section": self.section,
        }


@dataclass(frozen=True, slots=True)
class JobRequirements:
    required_skills: tuple[str, ...] = ()
    preferred_skills: tuple[str, ...] = ()
    mentioned_skills: tuple[str, ...] = ()
    ats_keywords: tuple[AtsKeyword, ...] = ()
    location_scope: Mapping[str, object] = field(default_factory=dict)
    analysis_hash: str = ""
    analysis_version: str = REQUIREMENTS_VERSION
    sections: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.analysis_version,
            "analysis_hash": self.analysis_hash,
            "required_skills": list(self.required_skills),
            "preferred_skills": list(self.preferred_skills),
            "mentioned_skills": list(self.mentioned_skills),
            "ats_keywords": [item.to_dict() for item in self.ats_keywords],
            "location_scope": dict(self.location_scope),
            "sections": list(self.sections),
        }


_EXTRACTOR: KeywordExtractor | None = None
_NORMALIZER: KeywordNormalizer | None = None


def _extractor() -> KeywordExtractor:
    global _EXTRACTOR
    if _EXTRACTOR is None:
        _EXTRACTOR = KeywordExtractor()
    return _EXTRACTOR


def _normalizer() -> KeywordNormalizer:
    global _NORMALIZER
    if _NORMALIZER is None:
        _NORMALIZER = KeywordNormalizer()
    return _NORMALIZER


def _canonical(keyword_id: str) -> str:
    skill = _normalizer()._by_id.get(keyword_id)
    return str(skill.get("canonical")) if skill else keyword_id


def _clean_heading(value: str) -> str:
    normalized = value.casefold().replace("’", "'")
    return re.sub(r"[^a-z0-9' ]+", " ", normalized).strip()


class _TextBlocks(HTMLParser):
    """Flatten HTML while keeping section headings on their own lines."""

    _BREAKS = frozenset(
        {"p", "div", "li", "ul", "ol", "section", "article", "br", "tr"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._skip += 1
        elif tag in self._BREAKS or re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip = max(0, self._skip - 1)
        elif tag in self._BREAKS or re.fullmatch(r"h[1-6]", tag):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def plain_description(description: str | None) -> str:
    """Readable posting text, even when a connector stored its HTML body."""
    body = unescape(description or "")
    if not re.search(r"</?[a-z][^>]*>", body, re.IGNORECASE):
        return body
    parser = _TextBlocks()
    try:
        parser.feed(body)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML must not lose the posting
        return re.sub(r"<[^>]+>", " ", body)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line)


def _heading_category(line: str) -> tuple[str | None, str]:
    """Return (category, remainder) when a line is a section heading."""
    stripped = line.strip().rstrip(":").strip()
    if not stripped:
        return None, ""
    match = _HEADING_RE.match(stripped)
    if not match:
        return None, line
    heading = _clean_heading(match.group("heading"))
    if not heading:
        return None, line
    # A heading must be a known heading or clearly one of the three groups.
    for candidate in _PREFERRED_HEADINGS:
        if heading == _clean_heading(candidate):
            return "preferred", ""
    for candidate in _REQUIRED_HEADINGS:
        if heading == _clean_heading(candidate):
            return "required", ""
    for candidate in _OTHER_HEADINGS:
        if heading == _clean_heading(candidate):
            return "other", ""
    # Inline headings ("Requirements: Python, Go") are handled before this.
    return None, line


def _inline_heading(line: str) -> tuple[str | None, str]:
    match = re.match(
        r"^\s*(?P<head>[A-Za-z][A-Za-z &'/’\-]{2,48})\s*:\s*(?P<rest>.+)$", line
    )
    if not match:
        return None, line
    heading = _clean_heading(match.group("head"))
    for candidate in _PREFERRED_HEADINGS:
        if heading == _clean_heading(candidate):
            return "preferred", match.group("rest")
    for candidate in _REQUIRED_HEADINGS:
        if heading == _clean_heading(candidate):
            return "required", match.group("rest")
    for candidate in _OTHER_HEADINGS:
        if heading == _clean_heading(candidate):
            return "other", match.group("rest")
    return None, line


def _importance(sentence: str, section: str) -> tuple[str, str]:
    if _PREFERRED_CUE.search(sentence):
        return "preferred", section
    if section == "preferred":
        return "preferred", section
    if section == "required" or _REQUIRED_CUE.search(sentence):
        return "required", section
    return "mentioned", section


def analyze_job(
    title: str,
    description: str,
    *,
    location_text: str | None = None,
    workplace_type: str | None = None,
) -> JobRequirements:
    """Analyze one posting into required/preferred/mentioned skills and ATS keywords."""
    body = plain_description(description)
    extractor = _extractor()
    importance_by_skill: dict[str, str] = {}
    phrase_by_skill: dict[str, str] = {}
    frequency: dict[str, int] = {}
    section_by_skill: dict[str, str] = {}
    sections_seen: list[str] = []
    section = "description"

    def consider(line: str) -> None:
        nonlocal section
        for sentence in _SENTENCE_SPLIT.split(line):
            text = sentence.strip()
            if not text:
                continue
            importance, observed_section = _importance(text, section)
            for keyword_id, start, end in extractor.mentions_with_spans(text):
                phrase = text[start:end]
                frequency[keyword_id] = frequency.get(keyword_id, 0) + 1
                if keyword_id not in phrase_by_skill:
                    phrase_by_skill[keyword_id] = phrase
                current = importance_by_skill.get(keyword_id)
                rank = _IMPORTANCE_RANK
                if current is None or rank[importance] > rank[current]:
                    importance_by_skill[keyword_id] = importance
                    section_by_skill[keyword_id] = observed_section or section
        if section not in sections_seen and section != "description":
            sections_seen.append(section)

    # The title is useful context but is never a hard requirement by itself.
    title_mentions = extractor.mentions(title or "")
    for keyword_id in title_mentions:
        frequency[keyword_id] = frequency.get(keyword_id, 0) + 1
        phrase_by_skill.setdefault(keyword_id, _canonical(keyword_id))
        importance_by_skill.setdefault(keyword_id, "mentioned")
        section_by_skill.setdefault(keyword_id, "title")

    for raw_line in body.splitlines():
        line = _BULLET_RE.sub("", raw_line).strip()
        if not line:
            continue
        inline_category, remainder = _inline_heading(line)
        if inline_category is not None:
            section = inline_category
            line = remainder
        else:
            category, remainder = _heading_category(line)
            if category is not None:
                section = category
                if remainder:
                    line = remainder
                else:
                    continue
        consider(line)

    required = sorted(
        (keyword for keyword, value in importance_by_skill.items() if value == "required"),
    )
    preferred = sorted(
        (keyword for keyword, value in importance_by_skill.items() if value == "preferred"),
    )
    mentioned = sorted(
        (keyword for keyword, value in importance_by_skill.items() if value == "mentioned"),
    )
    ats_keywords = tuple(
        AtsKeyword(
            keyword_id=keyword_id,
            canonical=_canonical(keyword_id),
            phrase=phrase_by_skill.get(keyword_id, _canonical(keyword_id)),
            importance=importance_by_skill[keyword_id],
            frequency=frequency.get(keyword_id, 1),
            section=section_by_skill.get(keyword_id, "description"),
        )
        for keyword_id in sorted(
            importance_by_skill,
            key=lambda value: (_IMPORTANCE_RANK[importance_by_skill[value]] * -1, value),
        )
    )
    scope = parse_location_scope(
        location_text, body, workplace_type=workplace_type
    )
    digest = analysis_hash_for(title, body, location_text, workplace_type)
    return JobRequirements(
        required_skills=tuple(required),
        preferred_skills=tuple(preferred),
        mentioned_skills=tuple(mentioned),
        ats_keywords=ats_keywords,
        location_scope=scope,
        analysis_hash=digest,
        sections=tuple(sections_seen),
    )


_IMPORTANCE_RANK = {"mentioned": 0, "preferred": 1, "required": 2}


def analysis_hash_for(
    title: str,
    description: str,
    location_text: str | None = None,
    workplace_type: str | None = None,
) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "version": REQUIREMENTS_VERSION,
                "title": title,
                "description": description or "",
                "location": location_text or "",
                "workplace": workplace_type or "",
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def requirement_coverage(
    skills: Iterable[str],
    statuses: Mapping[str, str],
    required: Iterable[str],
) -> tuple[float | None, list[str]]:
    """Coverage of required skills by candidate strength.

    ``None`` means the posting has no confidently extracted requirements, so the
    caller must not hide it.  A skill marked ``not_relevant`` is excluded from
    the denominator rather than counted as missing.
    """
    from app.domain.services.matching import SKILL_STATUS_STRENGTH

    required_values = [str(skill) for skill in required if str(skill)]
    if not required_values:
        return None, []
    known = set(str(skill) for skill in skills)
    denominator = 0
    numerator = 0.0
    missing: list[str] = []
    for skill in required_values:
        status = statuses.get(skill)
        if status == "not_relevant":
            continue
        denominator += 1
        if status in SKILL_STATUS_STRENGTH:
            strength = SKILL_STATUS_STRENGTH[status]
            numerator += 1.0 if strength is None else float(strength)
        elif skill in known:
            numerator += 1.0
        else:
            missing.append(skill)
    if denominator == 0:
        return None, []
    return numerator / denominator, missing


__all__ = [
    "AtsKeyword",
    "JobRequirements",
    "REQUIREMENTS_VERSION",
    "analysis_hash_for",
    "analyze_job",
    "plain_description",
    "requirement_coverage",
]
