"""Canonical job location eligibility.

A job board often writes the real restriction in prose ("Remote - India",
"must be located in Africa") while the structured location is only ``Remote``.
Treating every remote posting as available to every remote candidate is how a
Chicago candidate was shown Indian roles.  This module turns the location text
and the posting body into canonical eligibility tokens, so the visibility rule
can use country/region membership instead of a substring guess.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

#: Internal tokens are ISO-3166 alpha-2 where possible, plus the broad regions
#: recruiters actually write.  Display text is intentionally not stored here.
COUNTRY_NAMES: dict[str, str] = {
    "united states": "US",
    "united states of america": "US",
    "u.s.a.": "US",
    "u.s.": "US",
    "usa": "US",
    "us": "US",
    "america": "US",
    "canada": "CA",
    "mexico": "MX",
    "brazil": "BR",
    "argentina": "AR",
    "chile": "CL",
    "colombia": "CO",
    "peru": "PE",
    "united kingdom": "GB",
    "great britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "uk": "GB",
    "u.k.": "GB",
    "ireland": "IE",
    "germany": "DE",
    "deutschland": "DE",
    "france": "FR",
    "spain": "ES",
    "portugal": "PT",
    "netherlands": "NL",
    "holland": "NL",
    "belgium": "BE",
    "switzerland": "CH",
    "austria": "AT",
    "italy": "IT",
    "poland": "PL",
    "sweden": "SE",
    "norway": "NO",
    "denmark": "DK",
    "finland": "FI",
    "czech republic": "CZ",
    "czechia": "CZ",
    "romania": "RO",
    "ukraine": "UA",
    "israel": "IL",
    "turkey": "TR",
    "united arab emirates": "AE",
    "uae": "AE",
    "saudi arabia": "SA",
    "qatar": "QA",
    "india": "IN",
    "pakistan": "PK",
    "bangladesh": "BD",
    "sri lanka": "LK",
    "singapore": "SG",
    "malaysia": "MY",
    "indonesia": "ID",
    "philippines": "PH",
    "vietnam": "VN",
    "thailand": "TH",
    "japan": "JP",
    "south korea": "KR",
    "korea": "KR",
    "china": "CN",
    "hong kong": "HK",
    "taiwan": "TW",
    "australia": "AU",
    "new zealand": "NZ",
    "south africa": "ZA",
    "nigeria": "NG",
    "kenya": "KE",
    "ghana": "GH",
    "egypt": "EG",
    "morocco": "MA",
    "ethiopia": "ET",
    "tanzania": "TZ",
    "uganda": "UG",
    "rwanda": "RW",
    "senegal": "SN",
    "ivory coast": "CI",
    "côte d'ivoire": "CI",
    "cameroon": "CM",
    "zimbabwe": "ZW",
    "zambia": "ZM",
    "botswana": "BW",
    "namibia": "NA",
    "mozambique": "MZ",
    "angola": "AO",
    "tunisia": "TN",
    "algeria": "DZ",
}

#: Cities and states are a hint, never proof: a posting can name a city in a
#: different country.  They make "Chicago" and "IL" resolve to the US when no
#: country is written.
CITY_COUNTRY: dict[str, str] = {
    "chicago": "US",
    "illinois": "US",
    "new york": "US",
    "nyc": "US",
    "california": "US",
    "san francisco": "US",
    "seattle": "US",
    "austin": "US",
    "boston": "US",
    "denver": "US",
    "atlanta": "US",
    "los angeles": "US",
    "washington dc": "US",
    "toronto": "CA",
    "vancouver": "CA",
    "montreal": "CA",
    "london": "GB",
    "manchester": "GB",
    "edinburgh": "GB",
    "berlin": "DE",
    "munich": "DE",
    "hamburg": "DE",
    "paris": "FR",
    "amsterdam": "NL",
    "dublin": "IE",
    "madrid": "ES",
    "barcelona": "ES",
    "lisbon": "PT",
    "warsaw": "PL",
    "bangalore": "IN",
    "bengaluru": "IN",
    "hyderabad": "IN",
    "pune": "IN",
    "mumbai": "IN",
    "delhi": "IN",
    "new delhi": "IN",
    "gurgaon": "IN",
    "gurugram": "IN",
    "noida": "IN",
    "chennai": "IN",
    "kolkata": "IN",
    "tokyo": "JP",
    "singapore city": "SG",
    "sydney": "AU",
    "melbourne": "AU",
    "auckland": "NZ",
    "cape town": "ZA",
    "johannesburg": "ZA",
    "lagos": "NG",
    "nairobi": "KE",
    "cairo": "EG",
    "são paulo": "BR",
    "sao paulo": "BR",
}

US_STATES: dict[str, str] = {
    "al": "US", "ak": "US", "az": "US", "ar": "US", "ca": "US", "co": "US",
    "ct": "US", "de": "US", "fl": "US", "ga": "US", "hi": "US", "id": "US",
    "il": "US", "in": "US", "ia": "US", "ks": "US", "ky": "US", "la": "US",
    "me": "US", "md": "US", "ma": "US", "mi": "US", "mn": "US", "ms": "US",
    "mo": "US", "mt": "US", "ne": "US", "nv": "US", "nh": "US", "nj": "US",
    "nm": "US", "ny": "US", "nc": "US", "nd": "US", "oh": "US", "ok": "US",
    "or": "US", "pa": "US", "ri": "US", "sc": "US", "sd": "US", "tn": "US",
    "tx": "US", "ut": "US", "vt": "US", "va": "US", "wa": "US", "wv": "US",
    "wi": "US", "wy": "US",
}

#: Broad regions people and postings use.  Africa is listed so a US candidate
#: cannot match it; EMEA/APAC/LATAM are likewise explicit.
AFRICA = frozenset(
    {
        "DZ", "AO", "BJ", "BW", "BF", "BI", "CM", "CV", "CF", "TD", "KM",
        "CG", "CD", "CI", "DJ", "EG", "GQ", "ER", "SZ", "ET", "GA", "GM",
        "GH", "GN", "GW", "KE", "LS", "LR", "LY", "MG", "MW", "ML", "MR",
        "MU", "MA", "MZ", "NA", "NE", "NG", "RW", "ST", "SN", "SC", "SL",
        "SO", "ZA", "SS", "SD", "TZ", "TG", "TN", "UG", "ZM", "ZW",
    }
)
EUROPE = frozenset(
    {
        "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
        "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
        "RO", "SK", "SI", "ES", "SE", "GB", "CH", "NO", "IS", "UA",
    }
)
LATAM = frozenset(
    {
        "AR", "BO", "BR", "CL", "CO", "CR", "CU", "DO", "EC", "SV", "GT",
        "HN", "MX", "NI", "PA", "PY", "PE", "PR", "UY", "VE",
    }
)
APAC = frozenset(
    {
        "AU", "BD", "BN", "CN", "HK", "ID", "IN", "JP", "KH", "KR", "LA",
        "LK", "MM", "MY", "MN", "NP", "NZ", "PH", "PK", "SG", "TH", "TW",
        "VN",
    }
)
MIDDLE_EAST = frozenset({"AE", "BH", "IL", "IQ", "JO", "KW", "LB", "OM", "QA", "SA", "TR", "YE"})
EMEA = EUROPE | MIDDLE_EAST | AFRICA

REGION_COUNTRIES: dict[str, frozenset[str]] = {
    "AFRICA": AFRICA,
    "EU": EUROPE,
    "EMEA": EMEA,
    "APAC": APAC,
    "LATAM": LATAM,
}

REGION_ALIASES: dict[str, str] = {
    "africa": "AFRICA",
    "emea": "EMEA",
    "europe": "EU",
    "european union": "EU",
    "eu": "EU",
    "apac": "APAC",
    "asia pacific": "APAC",
    "asia-pacific": "APAC",
    "latam": "LATAM",
    "latin america": "LATAM",
    "south america": "LATAM",
}

_ALL_ALIASES: dict[str, str] = {
    **COUNTRY_NAMES,
    **CITY_COUNTRY,
    **{key: value for key, value in US_STATES.items()},
    **REGION_ALIASES,
}
_ALIAS_RE = re.compile(
    r"(?<![a-z0-9])("
    + "|".join(
        re.escape(alias)
        for alias in sorted(_ALL_ALIASES, key=len, reverse=True)
    )
    + r")(?![a-z0-9])",
    re.IGNORECASE,
)

_REMOTE_WORDS = re.compile(r"\b(remote|anywhere|work from home|wfh)\b", re.IGNORECASE)
_GLOBAL_RE = re.compile(
    r"\b(work from anywhere|remote anywhere|fully remote|globally remote|"
    r"worldwide|open globally|anywhere in the world)\b",
    re.IGNORECASE,
)
_RESTRICTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?:must|need(?:s)? to|required to|have to)\s+(?:be\s+)?"
        r"(?:located|reside|live|be based|work)\s+(?:in|within|from)\s+([^.;\n]+)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:open|available|limited|restricted)\s+(?:only\s+)?(?:to|for)\s+"
        r"(?:candidates|applicants|residents|people|employees)\s+"
        r"(?:who are\s+)?(?:based|located|residing|living)?\s*(?:in|within)?\s*"
        r"([^.;\n]+)",
        re.IGNORECASE,
    ),
    re.compile(r"\bremote\s+(?:within|in|from)\s+([^.;\n]+)", re.IGNORECASE),
    re.compile(
        r"\b(?:eligible|authorized|authorised|permitted|able)\s+to\s+work\s+"
        r"(?:in|within)\s+([^.;\n]+)",
        re.IGNORECASE,
    ),
    re.compile(r"\bright\s+to\s+work\s+(?:in|within)\s+([^.;\n]+)", re.IGNORECASE),
    re.compile(
        r"\b(?:must|require(?:s|d)?|need(?:s|ed)?)\s+(?:have\s+)?"
        r"(?:the\s+)?(?:legal\s+)?(?:right|authorization|authorisation)\s+"
        r"to\s+work\s+(?:in|within)\s+([^.;\n]+)",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:us|u\.s\.|united states)[- ]based\b", re.IGNORECASE),
    re.compile(
        r"\b(?:must|need(?:s)? to)\s+be\s+(?:a\s+)?(?:us|u\.s\.|united states)\s+"
        r"(?:citizen|resident|person)\b",
        re.IGNORECASE,
    ),
)
_REMOTE_LOCATION_RE = re.compile(
    r"(?:remote|anywhere)\s*(?:[-–—,(]\s*|\bin\b\s*)([^)\];]+)",
    re.IGNORECASE,
)
_LOCATION_FIRST_RE = re.compile(
    r"^([^,;()\-–—]+)\s*(?:[-–—,(]\s*|\s+)(?:remote|anywhere)\b",
    re.IGNORECASE,
)
_TRAILING_REMOTE_RE = re.compile(
    r"([^,;()\-–—]+)\s*[-–—]\s*remote\b",
    re.IGNORECASE,
)


def _clean_phrase(text: str) -> str:
    value = re.sub(r"\s+", " ", (text or "")).strip(" \t,;:.-–—()[]")
    value = re.sub(r"^(?:the|a|an)\s+", "", value, flags=re.IGNORECASE)
    return value


def canonical_tokens(text: str) -> list[str]:
    """Country/region tokens named in *text*, longest aliases first."""
    if not text:
        return []
    found: list[str] = []
    for match in _ALIAS_RE.finditer(text):
        token = _ALL_ALIASES.get(match.group(1).lower())
        if token and token not in found:
            found.append(token)
    return found


def canonical_country(text: str) -> str | None:
    """Best single country for a location/authorization phrase."""
    for token in canonical_tokens(text):
        if token in REGION_COUNTRIES:
            continue
        return token
    for token in canonical_tokens(text):
        if token in REGION_COUNTRIES:
            # A region is not a country; the caller must use membership.
            continue
    return None


def candidate_countries(preferences: Mapping[str, object] | None) -> list[str]:
    """Countries a candidate can work in, from explicit fields or home location."""
    if not preferences:
        return []
    values: list[str] = []
    for key in ("authorized_countries", "work_countries", "work_authorization_countries"):
        raw = preferences.get(key)
        if isinstance(raw, str):
            values.append(raw)
        elif isinstance(raw, (list, tuple, set)):
            values.extend(str(item) for item in raw if str(item).strip())
    home = preferences.get("home_location") or preferences.get("current_location")
    if isinstance(home, str):
        values.append(home)
    found: list[str] = []
    for value in values:
        for country in canonical_tokens(str(value)):
            if country not in REGION_COUNTRIES and country not in found:
                found.append(country)
    # ``locations`` doubles as acceptable locations in older profiles; its
    # country is a safe fallback when no explicit authorization field exists.
    fallback = preferences.get("locations")
    if not found and isinstance(fallback, (list, tuple, set)):
        for value in fallback:
            for country in canonical_tokens(str(value)):
                if country not in REGION_COUNTRIES and country not in found:
                    found.append(country)
    return found


def _country_in_scope(country: str, scope: str) -> bool:
    if country == scope:
        return True
    members = REGION_COUNTRIES.get(scope)
    return bool(members and country in members)


def scope_allows(scope: Mapping[str, object] | None, countries: Sequence[str]) -> bool | None:
    """Whether any authorized country is inside a parsed job scope.

    ``None`` means the scope cannot answer the question (unknown or global
    without a known candidate country).  A known foreign scope returns False.
    """
    if not scope or not countries:
        return None
    if str(scope.get("remote_scope") or "") == "global":
        return True
    raw_eligible = scope.get("eligible_countries")
    eligible = (
        [str(value) for value in raw_eligible if value]
        if isinstance(raw_eligible, (list, tuple))
        else []
    )
    if not eligible:
        return None
    return any(_country_in_scope(country, item) for country in countries for item in eligible)


def parse_location_scope(
    location_text: str | None,
    description: str | None = None,
    *,
    workplace_type: str | None = None,
) -> dict[str, object]:
    """Parse a posting's location and remote eligibility.

    Structured location text is authoritative; prose is used when the
    structured value only says "Remote".  Unknown stays unknown.
    """
    location = _clean_phrase(location_text or "")
    body = description or ""
    combined_location = location
    global_scope = bool(_GLOBAL_RE.search(body) or _GLOBAL_RE.search(location))
    remote = bool(_REMOTE_WORDS.search(location)) or str(workplace_type or "").lower() == "remote"
    if not remote:
        remote = bool(_REMOTE_WORDS.search(body))

    phrases: list[str] = []
    for match in _RESTRICTION_PATTERNS:
        for found in match.finditer(body):
            phrase = _clean_phrase(found.group(1) if found.lastindex else found.group(0))
            if phrase:
                phrases.append(phrase)
    for regex in (_REMOTE_LOCATION_RE, _LOCATION_FIRST_RE, _TRAILING_REMOTE_RE):
        for found in regex.finditer(location):
            phrase = _clean_phrase(found.group(1))
            if phrase:
                phrases.append(phrase)

    tokens: list[str] = []
    restriction_text = ""
    for phrase in [combined_location, *phrases]:
        for token in canonical_tokens(phrase):
            if token not in tokens:
                tokens.append(token)
    if phrases:
        restriction_text = phrases[0][:160]

    # A city/state is a country hint only when no explicit country is present.
    if not tokens and location:
        tokens = canonical_tokens(location)

    if global_scope and not tokens:
        return {
            "remote_scope": "global",
            "eligible_countries": [],
            "restriction_text": "",
            "confidence": 0.8,
        }
    if tokens:
        scope = (
            "region"
            if any(token in REGION_COUNTRIES for token in tokens)
            else "country"
        )
        return {
            "remote_scope": scope,
            "eligible_countries": tokens,
            "restriction_text": restriction_text or location[:160],
            "confidence": 0.9 if location else 0.7,
        }
    return {
        "remote_scope": "unknown",
        "eligible_countries": [],
        "restriction_text": "",
        "confidence": 0.0,
    }


__all__ = [
    "canonical_country",
    "canonical_tokens",
    "candidate_countries",
    "parse_location_scope",
    "scope_allows",
]
