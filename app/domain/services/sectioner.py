"""Resume sectioner (spec sec 10.2)."""
from __future__ import annotations

_SECTIONS = {
    "experience": ["experience", "work experience", "employment", "professional experience"],
    "education": ["education", "academic"],
    "skills": ["skills", "technical skills", "core competencies"],
    "projects": ["projects", "personal projects"],
    "certifications": ["certifications", "certificates", "licenses"],
    "summary": ["summary", "objective", "profile", "about"],
}


def section_text(text: str) -> dict[str, str]:
    """Split resume text into named sections by heading heuristics."""
    lines = (text or "").splitlines()
    sections: dict[str, list[str]] = {k: [] for k in _SECTIONS}
    sections["other"] = []
    current = "other"
    for line in lines:
        low = line.strip().lower().rstrip(":")
        matched = None
        for sec, keys in _SECTIONS.items():
            if low in keys or any(low.startswith(k) for k in keys):
                matched = sec
                break
        if matched:
            current = matched
            continue
        sections.setdefault(current, []).append(line)
    return {k: "\n".join(v).strip() for k, v in sections.items() if v}


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
