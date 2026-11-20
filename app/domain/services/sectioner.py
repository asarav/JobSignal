"""Resume sectioner and entity extraction (spec sec 10.2)."""
from __future__ import annotations

import re

_SECTIONS = {
    "experience": ["experience", "work experience", "employment", "professional experience", "work history", "career history"],
    "education": ["education", "academic", "academic background", "studies"],
    "skills": ["skills", "technical skills", "core competencies", "technologies", "tools", "expertise"],
    "projects": ["projects", "personal projects", "side projects", "portfolio"],
    "certifications": ["certifications", "certificates", "licenses", "credentials"],
    "summary": ["summary", "objective", "profile", "about", "about me", "overview"],
    "awards": ["awards", "honors", "achievements"],
    "languages": ["languages"],
}

_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|january|february|march|april|june|july|august|september|october|november|december"
_DATE_RE = re.compile(
    rf"(?P<start>(?:{_MONTHS})\s*\d{{2,4}}|\d{{4}})\s*[-–—to]+\s*(?P<end>(?:{_MONTHS})\s*\d{{2,4}}|\d{{4}}|present|current|now)",
    re.IGNORECASE,
)


def section_text(text: str) -> dict[str, str]:
    lines = (text or "").splitlines()
    sections: dict[str, list[str]] = {k: [] for k in _SECTIONS}
    sections["other"] = []
    current = "other"
    for line in lines:
        low = line.strip().lower().rstrip(":").rstrip(".").strip()
        matched = None
        for sec, keys in _SECTIONS.items():
            if low in keys or any(low == k or low.startswith(k + " ") for k in keys):
                matched = sec
                break
        if matched:
            current = matched
            continue
        sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items() if v and "\n".join(v).strip()}


def extract_dates(text: str) -> list[tuple[str, str]]:
    return [(m.group("start"), m.group("end")) for m in _DATE_RE.finditer(text or "")]


def extract_experience_entries(text: str) -> list[dict[str, str]]:
    """Heuristic: split experience section into entries by date ranges."""
    entries: list[dict[str, str]] = []
    lines = (text or "").splitlines()
    current: dict[str, str] | None = None
    for line in lines:
        s = line.strip()
        if not s:
            if current:
                entries.append(current)
                current = None
            continue
        m = _DATE_RE.search(s)
        if m and current is None:
            current = {"header": s, "start": m.group("start"), "end": m.group("end"), "bullets": ""}
        elif current is not None:
            if s.startswith(("-", "*", "•")):
                current["bullets"] = (current.get("bullets", "") + "\n" + s).strip()
            else:
                current["header"] = current.get("header", "") + " " + s
    if current:
        entries.append(current)
    return entries


def extract_skills_from_text(text: str) -> list[str]:
    from app.domain.services.keywords import KeywordExtractor

    return KeywordExtractor().extract(text)


def extract_experience_bullets(text: str) -> list[str]:
    bullets = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith(("-", "*", "•")):
            bullets.append(s.lstrip("-*• ").strip())
    return bullets
