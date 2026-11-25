"""Normalizer v1: turn a raw posting into the canonical job model (M1).

The normalizer is deliberately conservative: it only asserts a field when the
source payload or the title clearly supports it, and it records how confident
it is. Everything it cannot infer stays ``unknown`` rather than being guessed.

Strategy id is ``normalize.v1`` so provenance and content hashes stay stable
across runs (see ADR-0002).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from app.domain.models.common import Provenance
from app.domain.models.company import CompanyRef
from app.domain.models.enums import (
    EmploymentType,
    ExtractionStatus,
    PostingStatus,
    Seniority,
    WorkplaceType,
)
from app.domain.models.job import LocationRef, NormalizedJob, RawJob, SalaryRange
from app.domain.models.source import SourceConfig

if TYPE_CHECKING:
    from app.domain.services.keywords import KeywordExtractor

STRATEGY_ID = "normalize.v1"

_SENIORITY_PATTERNS: tuple[tuple[str, Seniority], ...] = (
    ("intern", Seniority.INTERN),
    ("internship", Seniority.INTERN),
    ("entry level", Seniority.ENTRY),
    ("junior", Seniority.ENTRY),
    ("associate", Seniority.ENTRY),
    ("staff", Seniority.STAFF),
    ("principal", Seniority.PRINCIPAL),
    ("director", Seniority.DIRECTOR),
    ("head of", Seniority.DIRECTOR),
    ("vp", Seniority.DIRECTOR),
    ("chief", Seniority.EXECUTIVE),
    ("cto", Seniority.EXECUTIVE),
    ("ceo", Seniority.EXECUTIVE),
    ("manager", Seniority.MANAGER),
    ("senior", Seniority.SENIOR),
    ("sr.", Seniority.SENIOR),
    ("lead", Seniority.SENIOR),
    ("mid", Seniority.MID),
    ("intermediate", Seniority.MID),
)

_EMPLOYMENT_PATTERNS: tuple[tuple[str, EmploymentType], ...] = (
    ("intern", EmploymentType.INTERNSHIP),
    ("internship", EmploymentType.INTERNSHIP),
    ("contract", EmploymentType.CONTRACT),
    ("temporary", EmploymentType.TEMPORARY),
    ("temp", EmploymentType.TEMPORARY),
    ("part time", EmploymentType.PART_TIME),
    ("part-time", EmploymentType.PART_TIME),
)

_WORD_RE = re.compile(r"[\W_]+")

# Letters that Unicode decomposition leaves untouched but that readers (and
# searches) expect under their base letter, e.g. Norwegian "Søker" -> "soker".
_LATIN_FOLDS = str.maketrans(
    {"ø": "o", "æ": "ae", "œ": "oe", "ł": "l", "đ": "d", "ð": "d", "þ": "th", "ı": "i"}
)

# Words that describe level rather than role; they are dropped when deriving a
# job family so "senior backend engineer" and "backend engineer" group together.
_SENIORITY_TOKENS = frozenset(
    {
        "senior",
        "sr",
        "junior",
        "jr",
        "staff",
        "principal",
        "lead",
        "head",
        "manager",
        "director",
        "vp",
        "chief",
        "intern",
        "entry",
        "mid",
        "intermediate",
        "associate",
    }
)


def normalize_title(title: str) -> str:
    """Lowercase, fold accents, strip punctuation, collapse whitespace.

    Accents fold to their base letters, so ``systèmes`` becomes ``systemes``
    instead of losing the accented letter and splitting the word. Scripts with
    no ASCII base (Cyrillic, CJK, ...) keep their letters rather than vanish.
    """
    decomposed = unicodedata.normalize("NFKD", title.casefold()).translate(_LATIN_FOLDS)
    folded = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _WORD_RE.sub(" ", folded).strip()


def title_family(normalized_title: str) -> str:
    """Coarse job family: the leading words that survive seniority stripping.

    ``"senior backend engineer"`` and ``"backend engineer"`` share the family
    ``"backend engineer"``. Used for grouping, never for identity decisions.
    """
    tokens = [t for t in normalized_title.split() if t not in _SENIORITY_TOKENS]
    return " ".join(tokens[:3])


def infer_seniority(title: str) -> Seniority:
    lowered = title.lower()
    for pattern, seniority in _SENIORITY_PATTERNS:
        if pattern in lowered:
            return seniority
    return Seniority.UNKNOWN


def infer_employment_type(title: str, payload: Mapping[str, Any] | None = None) -> EmploymentType:
    if payload:
        raw = str(payload.get("employmentType") or payload.get("employment_type") or "").lower()
        for candidate in EmploymentType:
            if candidate.value == raw:
                return candidate
    lowered = title.lower()
    for pattern, employment in _EMPLOYMENT_PATTERNS:
        if pattern in lowered:
            return employment
    return EmploymentType.UNKNOWN


def infer_workplace_type(
    title: str, payload: Mapping[str, Any] | None = None
) -> WorkplaceType:
    if payload:
        raw = str(payload.get("workplaceType") or payload.get("workplace_type") or "").lower()
        for candidate in WorkplaceType:
            if candidate.value == raw:
                return candidate
    # Extraction merges fields into a fresh payload, where locations live under
    # ``locations``; connectors normally use ``location``. Both shapes must
    # inform the workplace guess or every extracted location is ignored.
    raw_location = None
    if payload:
        raw_location = payload.get("location")
        if raw_location is None:
            raw_location = payload.get("locations")
    lowered = f"{title} {_location_text(raw_location)}".lower()
    if "hybrid" in lowered:
        return WorkplaceType.HYBRID
    if "remote" in lowered or "anywhere" in lowered or "work from home" in lowered:
        return WorkplaceType.REMOTE
    if "on-site" in lowered or "onsite" in lowered:
        return WorkplaceType.ONSITE
    return WorkplaceType.UNKNOWN


def _location_text(value: object) -> str:
    """Flatten a location value of any shape into searchable text."""
    if isinstance(value, Mapping):
        return " ".join(str(item) for item in value.values() if item is not None)
    if isinstance(value, (list, tuple, set)):
        return " ".join(_location_text(item) for item in value)
    return "" if value is None else str(value)


def parse_locations(payload: Mapping[str, Any] | None) -> list[LocationRef]:
    if not payload:
        return []
    raw = payload.get("location") or payload.get("locations")
    if raw is None:
        return []
    values = raw if isinstance(raw, (list, tuple)) else [raw]
    locations: list[LocationRef] = []
    for value in values:
        if isinstance(value, Mapping):
            name = str(value.get("name") or value.get("raw") or "")
            city = value.get("city")
            region = value.get("region") or value.get("state")
            country = value.get("country")
        else:
            name = str(value)
            city = region = country = None
        if not name:
            continue
        locations.append(
            LocationRef(raw=name, city=city, region=region, country=country)
        )
    return locations


def parse_salary(payload: Mapping[str, Any] | None) -> SalaryRange | None:
    if not payload:
        return None
    raw = payload.get("salary")
    if not isinstance(raw, Mapping):
        return None
    min_amount = _as_int(raw.get("minValue") or raw.get("min"))
    max_amount = _as_int(raw.get("maxValue") or raw.get("max"))
    if min_amount is None and max_amount is None:
        return None
    return SalaryRange(
        min_amount=min_amount,
        max_amount=max_amount,
        currency=str(raw["currency"]) if raw.get("currency") else None,
        period=str(raw["period"]) if raw.get("period") else None,
        annualized_min=None,
        annualized_max=None,
        raw_text=None,
    )


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_posted_at(payload: Mapping[str, Any] | None) -> datetime | None:
    if not payload:
        return None
    for key in (
        "posted_at",
        "postedAt",
        "created_at",
        "createdAt",
        "updated_at",
        "updatedAt",
        "publishedAt",
    ):
        value = payload.get(key)
        if isinstance(value, (int, float)):
            timestamp = float(value)
            # ATS feeds differ: Lever sends epoch milliseconds, while most
            # others send seconds. Treat values beyond year-5138 in seconds
            # as milliseconds instead of letting Windows raise OSError.
            if abs(timestamp) >= 100_000_000_000:
                timestamp /= 1000
            try:
                return datetime.fromtimestamp(timestamp, tz=UTC)
            except (OSError, OverflowError, ValueError):
                continue
        if isinstance(value, str) and value:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                continue
    return None


def company_name_for_source(source: SourceConfig) -> str:
    """Best-effort company name for a source.

    Prefers the ATS tenant key (stable, e.g. ``acme``); otherwise the last
    meaningful path segment of the board URL, else the host's first label.
    """
    if source.ats_tenant_key:
        return source.ats_tenant_key
    parsed = urlparse(source.url)
    parts = [part for part in parsed.path.split("/") if part]
    if parts:
        return parts[-1]
    host = parsed.netloc.split(".")[0]
    return host or source.id


def company_id_for(normalized_name: str) -> str:
    """Stable company id derived from the normalized company name.

    The same company must map to one row across sources and crawls, otherwise
    every crawl would create a duplicate company.
    """
    digest = hashlib.sha256(normalized_name.strip().lower().encode("utf-8")).hexdigest()
    return f"co_{digest[:16]}"


_SKILL_EXTRACTOR: KeywordExtractor | None = None


def requirement_skills(description: str) -> list[str]:
    """Skill ids written in a posting's description (taxonomy v1).

    Job descriptions are not resume sections, so the whole text is scanned.
    The extractor is lazy and shared: loading the taxonomy on every posting
    would dominate a crawl.
    """
    global _SKILL_EXTRACTOR
    if not description:
        return []
    if _SKILL_EXTRACTOR is None:
        from app.domain.services.keywords import KeywordExtractor

        _SKILL_EXTRACTOR = KeywordExtractor()
    extractor = _SKILL_EXTRACTOR
    return list(extractor.mentions(description))[:40]


def normalize_job(
    raw: RawJob,
    source: SourceConfig,
    *,
    company_name: str,
    now: datetime,
) -> NormalizedJob:
    """Build the canonical job record from a raw posting."""
    payload: Mapping[str, Any] = raw.payload or {}
    title = (raw.title or payload.get("title") or "").strip()
    if not title:
        title = "Untitled"
    normalized_title = normalize_title(title)
    description_text = raw.body_text or payload.get("description") or ""
    locations = parse_locations(payload)
    workplace = infer_workplace_type(title, payload)
    employment = infer_employment_type(title, payload)
    seniority = infer_seniority(title)
    salary = parse_salary(payload)
    posted_at = parse_posted_at(payload)

    found: list[str] = ["title", "canonical_url"]
    confidence: dict[str, float] = {"title": 0.9, "canonical_url": 1.0}
    if description_text:
        found.append("description_text")
        confidence["description_text"] = 0.8
    if locations:
        found.append("locations")
        confidence["locations"] = 0.7
    if workplace is not WorkplaceType.UNKNOWN:
        found.append("workplace_type")
        confidence["workplace_type"] = 0.7
    if employment is not EmploymentType.UNKNOWN:
        found.append("employment_type")
        confidence["employment_type"] = 0.7
    if seniority is not Seniority.UNKNOWN:
        found.append("seniority")
        confidence["seniority"] = 0.7
    if salary is not None:
        found.append("salary")
        confidence["salary"] = 0.6
    if posted_at is not None:
        found.append("posted_at")
        confidence["posted_at"] = 0.6

    completeness = len(found) / 9
    status = ExtractionStatus.COMPLETE if completeness >= 0.7 else ExtractionStatus.PARTIAL

    provenance = (
        Provenance(
            source_id=source.id,
            strategy_id=STRATEGY_ID,
            plugin_id=source.connector_id or "connector.unknown",
            plugin_version="1.0.0",
            url=raw.ref.url,
            fetched_at=now,
            content_hash=content_hash_for(title, description_text),
        ),
    )

    return NormalizedJob(
        company_ref=CompanyRef(name=company_name),
        title=title,
        normalized_title=normalized_title,
        title_family=title_family(normalized_title),
        description_text=description_text,
        description_html=raw.body_html,
        locations=locations,
        workplace_type=workplace,
        employment_type=employment,
        salary=salary,
        posted_at=posted_at,
        source_id=source.id,
        source_posting_id=raw.ref.source_posting_id,
        canonical_url=raw.ref.url,
        original_urls=[raw.ref.url],
        department=None,
        seniority=seniority,
        required_skills=requirement_skills(description_text),
        preferred_skills=[],
        extraction_status=status,
        extraction_confidence=round(completeness, 2),
        field_confidence=confidence,
        provenance=provenance,
        posting_status_hint=PostingStatus.ACTIVE,
        fetched_at=now,
    )


def content_hash_for(title: str, description_text: str) -> str:
    """Hash used to detect 'this posting did not change' between crawls."""
    return hashlib.sha256(f"{title}\n{description_text}".encode()).hexdigest()
