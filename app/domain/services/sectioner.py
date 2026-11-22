"""Resume sectioner and entity extraction (spec sec 10.2)."""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date

_SECTIONS = {
    "experience": ["experience", "work experience", "employment", "professional experience", "work history", "career history", "professional background"],
    "education": ["education", "academic", "academic background", "studies", "academic history"],
    "skills": ["skills", "technical skills", "core competencies", "technologies", "tools", "expertise", "technical expertise", "key skills"],
    "projects": ["projects", "personal projects", "side projects", "portfolio", "open source"],
    "certifications": ["certifications", "certificates", "licenses", "credentials", "professional certifications"],
    "summary": ["summary", "objective", "profile", "about", "about me", "overview", "professional summary"],
    "awards": ["awards", "honors", "achievements", "accomplishments"],
    "languages": ["languages", "spoken languages"],
    "contact": ["contact", "contact information", "personal information"],
}

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
# A date is a month name with a year, a numeric month/year, or a bare year. The
# month alternation is built from the known table so words such as "Python 3"
# are not mistaken for dates.
_DATE_TOKEN = rf"(?:(?:{_MONTH_ALT})\.?\s*,?\s*\d{{2,4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
_DATE_RE = re.compile(
    rf"(?P<start>{_DATE_TOKEN})\s*(?:[-–—]|to)\s*(?P<end>{_DATE_TOKEN}|present|current|now|today)",
    re.IGNORECASE,
)

_BULLET_PREFIXES = ("-", "*", "•", "–", "—")

# Job-title vocabulary used to split an experience header into employer/title.
_TITLE_WORDS = (
    "engineer", "developer", "manager", "analyst", "scientist", "designer",
    "consultant", "architect", "director", "administrator", "specialist",
    "officer", "coordinator", "accountant", "recruiter", "technician",
    "researcher", "professor", "teacher", "nurse", "writer", "editor",
    "marketer", "programmer", "devops", "sre", "cto", "ceo", "cfo", "coo",
    "president", "intern", "apprentice",
)
_SENIORITY_WORDS = frozenset(
    {
        "senior", "sr", "staff", "principal", "lead", "junior", "jr", "chief",
        "head", "associate", "assistant", "vice", "vp", "deputy", "executive",
    }
)
_TITLE_MODIFIERS = frozenset(
    {
        "software", "backend", "back-end", "frontend", "front-end", "full",
        "stack", "data", "machine", "learning", "security", "cloud", "devops",
        "site", "reliability", "product", "project", "program", "business",
        "systems", "network", "database", "qa", "test", "support", "sales",
        "marketing", "financial", "operations", "research", "electrical",
        "mechanical", "civil", "clinical", "registered", "technical", "mobile",
        "platform", "infrastructure", "application", "api", "ui", "ux",
    }
)
_TITLE_WORD_RE = re.compile(rf"\b(?:{'|'.join(_TITLE_WORDS)})\b", re.IGNORECASE)
_WORD_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z+#./-]*")

# Education: degree token plus an optional "of <subject>"/"in <field>".
_DEGREE_RE = re.compile(
    r"\b(bachelor(?:'s)?|master(?:'s)?|b\.?\s?s\.?\s?c?|m\.?\s?s\.?\s?c?|mba|ph\.?\s?d\.?|doctorate|associate(?:'s)?|diploma|certificate)\b",
    re.IGNORECASE,
)
_DEGREE_OF_RE = re.compile(
    r"\bof\s+(science|arts|engineering|technology|business administration|fine arts)\b",
    re.IGNORECASE,
)
_FIELD_RE = re.compile(r"\bin\s+([^,|•;\n]+)", re.IGNORECASE)

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE_CANDIDATE_RE = re.compile(r"(?<![\w.])\+?\d[\d\s().\-]{6,}\d(?![\w.])")
_YEAR_RANGE_RE = re.compile(r"^\d{4}\s*[-–—]\s*\d{4}$")
_URL_RE = re.compile(r"https?://[^\s<>\"']+|www\.[^\s<>\"']+|linkedin\.com/in/[^\s<>\"']+|github\.com/[^\s<>\"']+")
_NAME_RE = re.compile(r"^[A-Z][A-Za-z'’\-]+(?:\s+[A-Z][A-Za-z'’\-]+){1,3}$")
_HEADING_WORDS = {alias for aliases in _SECTIONS.values() for alias in aliases}


def parse_date_token(tok: str) -> date | None:
    """Parse a resume date token ("Jan 2020", "01/2020", "2020", "Present")."""
    tok = (tok or "").strip().lower().rstrip(".")
    if tok in {"present", "current", "now", "today"}:
        return date.today()
    m = re.match(r"(\d{1,2})/(\d{4})", tok)
    if m:
        try:
            return date(int(m.group(2)), int(m.group(1)), 1)
        except ValueError:
            return None
    # "Month Year" or "Month, Year"
    m = re.match(r"([a-z]+)\s*,?\s*(\d{2,4})", tok)
    if m:
        name = m.group(1)
        mon = _MONTHS.get(name) or _MONTHS.get(name[:3])
        if mon:
            y = int(m.group(2))
            if y < 100:
                y += 2000
            try:
                return date(y, mon, 1)
            except ValueError:
                return None
    # bare year
    m = re.match(r"(\d{4})", tok)
    if m:
        try:
            return date(int(m.group(1)), 1, 1)
        except ValueError:
            return None
    return None


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


def extract_contact(text: str) -> dict[str, str]:
    """Contact details from the resume header (stored, never used in matching)."""
    text = text or ""
    contact: dict[str, str] = {}
    email = _EMAIL_RE.search(text)
    if email:
        contact["email"] = email.group(0)
    phones: list[str] = []
    for candidate in _PHONE_CANDIDATE_RE.finditer(text):
        value = candidate.group(0).strip()
        digits = re.sub(r"\D", "", value)
        if 9 <= len(digits) <= 15 and not _YEAR_RANGE_RE.match(value):
            phones.append(value)
    if phones:
        contact["phone"] = max(phones, key=len)
    for url in _URL_RE.findall(text):
        low = url.lower()
        if "linkedin.com" in low:
            contact.setdefault("linkedin", url)
        elif "github.com" in low:
            contact.setdefault("github", url)
        else:
            contact.setdefault("website", url)
    # A resume header starts with the candidate name: 2-4 capitalized words on
    # one of the first few lines, and not a section heading.
    for line in text.splitlines()[:6]:
        value = line.strip()
        if _NAME_RE.match(value) and value.lower() not in _HEADING_WORDS:
            contact["name"] = value
            break
    return contact


def extract_dates(text: str) -> list[tuple[str, str]]:
    return [(m.group("start"), m.group("end")) for m in _DATE_RE.finditer(text or "")]


def extract_experience_entries(text: str) -> list[dict[str, str]]:
    """Split the experience section into entries by date ranges.

    Header lines before the first date range are kept with that entry. A new
    date range (or a blank line) closes the current entry.
    """
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    pending: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s:
            # A blank line often separates a job header from its bullets, so only
            # close an entry once it actually has bullets.
            if current is not None and current.get("bullets"):
                entries.append(current)
                current = None
            pending = []
            continue
        if s.startswith(_BULLET_PREFIXES):
            if current is not None:
                current["bullets"] = (current.get("bullets", "") + "\n" + s).strip()
            continue
        m = _DATE_RE.search(s)
        if m:
            if current is not None:
                entries.append(current)
            header = " ".join([*pending, s]).strip()
            current = {"header": header, "start": m.group("start"), "end": m.group("end"), "bullets": ""}
            pending = []
        elif current is not None:
            current["header"] = (current.get("header", "") + " " + s).strip()
        else:
            pending.append(s)
    if current is not None:
        entries.append(current)
    return entries


def _merge_ranges(ranges: list[tuple[date, date]]) -> list[tuple[date, date]]:
    """Merge overlapping/adjacent date ranges so time is never double-counted."""
    merged: list[tuple[date, date]] = []
    for start, end in sorted(ranges):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def entry_date_range(entry: dict[str, str]) -> tuple[date, date] | None:
    start = parse_date_token(entry.get("start", ""))
    end = parse_date_token(entry.get("end", ""))
    if start and end and end >= start:
        return start, end
    return None


def compute_years_experience(entries: list[dict[str, str]]) -> float | None:
    """Total years across merged, non-overlapping employment ranges (spec sec 10.2)."""
    merged = _merge_ranges(
        [rng for entry in entries if (rng := entry_date_range(entry)) is not None]
    )
    if not merged:
        return None
    total_days = sum((end - start).days for start, end in merged)
    return round(total_days / 365.25, 1)


def compute_skill_tenure(
    entries: list[dict[str, str]],
    skill_id: str,
    mentions: Callable[[str], list[str]],
    today: date | None = None,
) -> tuple[float | None, float | None]:
    """Return ``(duration_years, recency_years)`` for a skill (spec sec 10.3).

    Duration is the merged time spent in roles whose header/bullets mention the
    skill; recency is the time since the most recent of those roles ended.
    """
    ranges: list[tuple[date, date]] = []
    for entry in entries:
        text = " ".join(
            part for part in (entry.get("header", ""), entry.get("bullets", "")) if part
        )
        if skill_id not in mentions(text):
            continue
        rng = entry_date_range(entry)
        if rng is not None:
            ranges.append(rng)
    if not ranges:
        return None, None
    merged = _merge_ranges(ranges)
    duration = round(sum((end - start).days for start, end in merged) / 365.25, 1)
    latest_end = max(end for _start, end in merged)
    reference = today or date.today()
    recency = round(max(0, (reference - latest_end).days) / 365.25, 1)
    return duration, recency


def _clean_piece(text: str) -> str | None:
    cleaned = re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip(" \t,;|·-–—")
    return cleaned or None


def parse_experience_header(header: str) -> tuple[str | None, str | None]:
    """Split an experience header into ``(employer, title)`` (spec sec 10.2).

    Handles the common shapes: ``Acme Corp — Senior Engineer``,
    ``Senior Engineer, Acme Corp`` and ``Acme Corp\nSenior Engineer Jan 2020 - …``.
    """
    text = _clean_piece(_DATE_RE.sub(" ", header or ""))
    if not text:
        return None, None
    match = _TITLE_WORD_RE.search(text)
    if match is None:
        return text, None
    title_start = match.start()
    previous = [token for token in _WORD_TOKEN_RE.finditer(text[: match.start()])]
    for token in reversed(previous):
        word = token.group(0).lower().rstrip(".")
        if word in _SENIORITY_WORDS or word in _TITLE_MODIFIERS:
            title_start = token.start()
        else:
            break
    parts = re.split(r"\s*[,|•·]\s*|\s+[–—]\s+|\s+-\s+", text[title_start:], maxsplit=1)
    title = _clean_piece(parts[0])
    remainder = _clean_piece(parts[1]) if len(parts) > 1 else None
    before = _clean_piece(text[:title_start])
    employer = before or remainder or _clean_piece(text[match.end() :])
    if employer and len(employer) > 80:
        employer = employer[:80].rstrip()
    return employer, title


def extract_education_entries(text: str) -> list[dict[str, str]]:
    """Education records: institution, degree, field and dates (spec sec 10.2)."""
    entries: list[dict[str, str]] = []
    pending: list[str] = []
    fill_next: dict[str, str] | None = None
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        degree_match = _DEGREE_RE.search(raw)
        if degree_match is None:
            if fill_next is not None:
                dates = _DATE_RE.search(raw)
                if dates and not fill_next.get("start"):
                    fill_next["start"] = dates.group("start")
                    fill_next["end"] = dates.group("end")
                if not fill_next.get("institution"):
                    fill_next["institution"] = _clean_piece(_DATE_RE.sub(" ", raw)) or ""
                fill_next = None
                continue
            pending.append(raw)
            pending = pending[-2:]
            continue
        dates = _DATE_RE.search(raw)
        stripped = _DATE_RE.sub(" ", raw)
        degree = degree_match.group(0)
        of_match = _DEGREE_OF_RE.search(stripped[degree_match.end() :])
        if of_match:
            degree = f"{degree} of {of_match.group(1)}"
        field_match = _FIELD_RE.search(stripped)
        trailing = _clean_piece(stripped[degree_match.end() :])
        if field_match is not None:
            field = _clean_piece(field_match.group(1)) or ""
        elif trailing and not of_match and not trailing.lower().startswith("of "):
            field = trailing
        else:
            field = ""
        before = _clean_piece(stripped[: degree_match.start()])
        institution = before or (_clean_piece(pending[-1]) if pending else None)
        entry = {
            "institution": institution or "",
            "degree": _clean_piece(degree) or "",
            "field": field,
            "start": dates.group("start") if dates else "",
            "end": dates.group("end") if dates else "",
            "confidence": "0.7" if institution else "0.5",
        }
        entries.append(entry)
        fill_next = entry if not entry["institution"] else None
        pending = []
    return entries


def extract_profile_items(text: str) -> list[dict[str, str]]:
    """Certification/project entries: a name line plus its detail lines."""
    items: list[dict[str, str]] = []
    for line in (text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        if raw.startswith(_BULLET_PREFIXES) and items:
            detail = raw.lstrip("-*•–— ").strip()
            current = items[-1]
            current["details"] = (current["details"] + "\n" + detail).strip()
        else:
            items.append({"name": raw.lstrip("-*•–— ").strip(), "details": ""})
    return items


def extract_skills_from_text(text: str) -> list[str]:
    from app.domain.services.keywords import KeywordExtractor

    return KeywordExtractor().extract(text)


def extract_experience_bullets(text: str) -> list[str]:
    bullets = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith(_BULLET_PREFIXES):
            bullets.append(s.lstrip("-*•–— ").strip())
    return bullets
