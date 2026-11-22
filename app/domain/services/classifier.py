"""Source classifier: map a URL to a connector id (M1)."""

from __future__ import annotations

from app.domain.models.source import PageSniff

KNOWN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("greenhouse.io", "ats.greenhouse"),
    ("boards.greenhouse.io", "ats.greenhouse"),
    ("jobs.ashbyhq.com", "ats.ashby"),
    ("ashbyhq.com", "ats.ashby"),
    ("jobs.lever.co", "ats.lever"),
    ("lever.co", "ats.lever"),
    ("smartrecruiters.com", "ats.smartrecruiters"),
    ("myworkdayjobs.com", "ats.workday"),
    ("myworkdaysite.com", "ats.workday"),
    ("icims.com", "ats.icims"),
    ("recruiting.ultipro.com", "ats.ultipro"),
    ("recruiting2.ultipro.com", "ats.ultipro"),
    ("recruiting3.ultipro.com", "ats.ultipro"),
    ("recruiting.ultipro.ca", "ats.ultipro"),
    ("apply.workable.com", "ats.workable"),
    ("workable.com", "ats.workable"),
    ("bamboohr.com", "ats.bamboohr"),
    ("oraclecloud.com/hcmui/candidateexperience", "ats.oracle_recruiting"),
    ("oraclecloud.com/hcmrestapi", "ats.oracle_recruiting"),
    ("eightfold.ai", "ats.eightfold"),
    ("phenompeople.com", "ats.phenom"),
    ("amazon.jobs", "ats.amazon"),
    ("taleo.net/careersection", "ats.taleo"),
    ("applytojob.com", "ats.jazzhr"),
    ("recruiting.paylocity.com/recruiting/jobs/all/", "ats.paylocity"),
)


def classify_url(url: str, sniff: PageSniff | None = None) -> tuple[str | None, float]:
    lowered = url.lower()
    for pattern, connector_id in KNOWN_PATTERNS:
        if pattern in lowered:
            return connector_id, 0.9
    if sniff is not None and sniff.html_excerpt:
        html = sniff.html_excerpt.lower()
        for pattern, connector_id in KNOWN_PATTERNS:
            if pattern in html:
                return connector_id, 0.6
        if any(marker in html for marker in ("ddokey", "refinesearch", "phenompeople")):
            return "ats.phenom", 0.6
    return None, 0.0
