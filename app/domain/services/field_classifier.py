"""Field recognition and classification (spec §13.3).

Signals are combined in a fixed order — ``autocomplete`` first (it is the only
signal the page author states explicitly), then name/id, then label and
accessible name, then type. The result is a classification, a confidence and
the evidence that produced it, so the review panel can explain itself.

Blocked classes are matched *before* fillable ones: a field labelled "Gender"
inside a section called "Personal details" must never be treated as a name.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.domain.models.browser import FieldClassification, FieldOption

#: autocomplete token -> (class, key). Covers the WHATWG tokens browsers ship.
AUTOCOMPLETE: dict[str, tuple[str, str]] = {
    "given-name": ("fillable_profile", "first_name"),
    "additional-name": ("fillable_profile", "middle_name"),
    "family-name": ("fillable_profile", "last_name"),
    "name": ("fillable_profile", "full_name"),
    "email": ("fillable_profile", "email"),
    "tel": ("fillable_profile", "phone"),
    "tel-national": ("fillable_profile", "phone"),
    "url": ("fillable_profile", "website"),
    "organization": ("fillable_profile", "current_employer"),
    "organization-title": ("fillable_profile", "current_title"),
    "street-address": ("fillable_profile", "address_line1"),
    "address-line1": ("fillable_profile", "address_line1"),
    "address-level2": ("fillable_profile", "city"),
    "address-level1": ("fillable_profile", "region"),
    "postal-code": ("fillable_profile", "postal_code"),
    "country-name": ("fillable_profile", "country"),
    "country": ("fillable_profile", "country"),
    "bday": ("blocked_sensitive", "date_of_birth"),
}

#: Text patterns, evaluated in order. First match wins.
RULES: tuple[tuple[str, str, str | None, str], ...] = (
    # Blocked legal: attestations and consents.
    (
        r"\b(agree|certif(y|ication)|acknowledg|consent|terms|privacy|background check|"
        r"arbitration|non[- ]compete|signature|e[- ]sign|attest)",
        "blocked_legal",
        None,
        "legal_attestation",
    ),
    # Blocked demographics: voluntary self-identification.
    (
        r"\b(gender|race|ethnic(ity)?|veteran|disabilit\w*|sexual orientation|"
        r"religion|pronouns?|transgender|age range|age bracket|what is your age)",
        "blocked_demographic",
        None,
        "demographic",
    ),
    # Blocked sensitive identifiers.
    (
        r"\b(ssn|social security|national id|passport|bank|routing number|"
        r"account number|password|date of birth|dob|security question)\b",
        "blocked_sensitive",
        None,
        "sensitive_identifier",
    ),
    # User-supplied answers: only ever filled from an explicit stored answer.
    (
        r"\b(work authori[sz]ation|authori[sz]ed to work|eligible to work|"
        r"right to work)\b",
        "user_supplied_answer",
        "work_authorization",
        "user_answer",
    ),
    (
        r"\b(visa|sponsorship|require sponsorship)\b",
        "user_supplied_answer",
        "visa_sponsorship",
        "user_answer",
    ),
    (
        r"\b(notice period|notice)\b",
        "user_supplied_answer",
        "notice_period",
        "user_answer",
    ),
    (
        r"\b(start date|available (from|to start)|earliest start)\b",
        "user_supplied_answer",
        "start_date",
        "user_answer",
    ),
    (
        r"\b(desired salary|salary expectation|expected salary)\b",
        "user_supplied_answer",
        "desired_salary",
        "user_answer",
    ),
    (
        r"\brelocation\b",
        "user_supplied_answer",
        "relocation",
        "user_answer",
    ),
    (r"\b(first name|given name)\b", "fillable_profile", "first_name", "name"),
    (r"\b(last name|surname|family name)\b", "fillable_profile", "last_name", "name"),
    (r"\b(full name|legal name|your name)\b", "fillable_profile", "full_name", "name"),
    (r"\b(e[- ]?mail)\b", "fillable_profile", "email", "email"),
    (r"\b(phone|mobile|telephone)\b", "fillable_profile", "phone", "phone"),
    (r"\b(linked ?in)\b", "fillable_profile", "linkedin_url", "profile_url"),
    (r"\b(git ?hub)\b", "fillable_profile", "github_url", "profile_url"),
    (
        r"\b(portfolio|personal (web)?site|website)\b",
        "fillable_profile",
        "website",
        "profile_url",
    ),
    (
        r"\b(video (link|url|reel|demo)|showreel|vimeo|youtube)\b",
        "fillable_profile",
        "video_url",
        "profile_url",
    ),
    (
        r"\b(current location|your location|where are you based)\b",
        "fillable_profile",
        "location",
        "address",
    ),
    (
        r"\b(street address|address line 1|address)\b",
        "fillable_profile",
        "address_line1",
        "address",
    ),
    (r"\b(city|town)\b", "fillable_profile", "city", "address"),
    (r"\b(state|province|region)\b", "fillable_profile", "region", "address"),
    (r"\b(zip|postal)\b", "fillable_profile", "postal_code", "address"),
    (r"\bcountry\b", "fillable_profile", "country", "address"),
    (
        r"\b(current (company|employer)|present employer)\b",
        "fillable_profile",
        "current_employer",
        "employment",
    ),
    (
        r"\b(current (job )?title|present title)\b",
        "fillable_profile",
        "current_title",
        "employment",
    ),
    (r"\b(school|university|college|institution)\b", "fillable_profile", "school", "education"),
    (r"\b(degree|qualification)\b", "fillable_profile", "degree", "education"),
    (r"\b(major|field of study)\b", "fillable_profile", "major", "education"),
    (r"\bgpa\b", "fillable_profile", "gpa", "education"),
    (
        r"\b(years? of experience|experience \(years\))\b",
        "fillable_profile",
        "years_experience",
        "experience",
    ),
    # Open questions are never generated (§13.3).
    (
        r"\b(why (do you|us|are you)|cover letter|tell us about|describe (a|your)|"
        r"additional information|anything else)\b",
        "free_text_unknown",
        None,
        "free_text",
    ),
)

#: Type-only fallbacks when no textual signal matched.
KIND_FALLBACKS: dict[str, tuple[str, str | None, float]] = {
    "email": ("fillable_profile", "email", 0.90),
    "tel": ("fillable_profile", "phone", 0.88),
}

SENSITIVE_FILE_HINTS = re.compile(r"\b(resume|cv|curriculum)\b", re.IGNORECASE)
COVER_LETTER_HINTS = re.compile(r"\bcover letter\b", re.IGNORECASE)


def _text(*values: str | None) -> str:
    return " ".join(value for value in values if value).strip().lower()


def _match(token: str) -> tuple[str, str | None, str] | None:
    for pattern, field_class, key, evidence in RULES:
        if re.search(pattern, token):
            return field_class, key, evidence
    return None


def classify_field(
    *,
    kind: str,
    label: str | None = None,
    accessible_name: str | None = None,
    name_attr: str | None = None,
    id_attr: str | None = None,
    autocomplete_attr: str | None = None,
    placeholder: str | None = None,
    description: str | None = None,
    section_hint: str | None = None,
    options: Sequence[FieldOption] = (),
) -> FieldClassification:
    """Classify one control; never raises, worst case ``unknown``."""
    autocomplete = (autocomplete_attr or "").split()[-1] if autocomplete_attr else ""
    mapped = AUTOCOMPLETE.get(autocomplete)
    if mapped is not None:
        auto_class, auto_key = mapped
        return FieldClassification(
            field_class=auto_class,
            confidence=0.98,
            evidence=(f"autocomplete:{autocomplete}",),
            key=auto_key,
        )

    if kind == "file":
        haystack = _text(label, accessible_name, name_attr, placeholder)
        if COVER_LETTER_HINTS.search(haystack):
            return FieldClassification(
                field_class="fillable_profile",
                confidence=0.95,
                evidence=("file:cover_letter",),
                key="cover_letter_file",
            )
        if SENSITIVE_FILE_HINTS.search(haystack):
            return FieldClassification(
                field_class="fillable_profile",
                confidence=0.95,
                evidence=("file:resume",),
                key="resume_file",
            )
        return FieldClassification(
            field_class="unknown",
            confidence=0.2,
            evidence=("file:unrecognised",),
        )

    label_text = _text(label, accessible_name)
    attr_text = _text(name_attr, id_attr, placeholder)
    hint_text = _text(description, section_hint)

    # Blocked classes win over every fillable interpretation.
    for token, evidence_source in (
        (label_text, "label"),
        (attr_text, "attribute"),
        (hint_text, "context"),
    ):
        found = _match(token)
        if found is None:
            continue
        field_class, key, evidence = found
        if field_class.startswith("blocked") or field_class == "user_supplied_answer":
            confidence = 0.95 if evidence_source == "label" else 0.88
            return FieldClassification(
                field_class=field_class,
                confidence=confidence,
                evidence=(f"{evidence_source}:{evidence}",),
                key=key,
            )

    if label_text:
        found = _match(label_text)
        if found is not None:
            field_class, key, evidence = found
            return FieldClassification(
                field_class=field_class,
                confidence=0.93,
                evidence=(f"label:{evidence}",),
                key=key,
            )

    if attr_text:
        found = _match(attr_text)
        if found is not None:
            field_class, key, evidence = found
            return FieldClassification(
                field_class=field_class,
                confidence=0.87,
                evidence=(f"attribute:{evidence}",),
                key=key,
            )

    fallback = KIND_FALLBACKS.get(kind)
    if fallback is not None:
        field_class, key, confidence = fallback
        return FieldClassification(
            field_class=field_class,
            confidence=confidence,
            evidence=(f"type:{kind}",),
            key=key,
        )

    if hint_text:
        found = _match(hint_text)
        if found is not None:
            field_class, key, evidence = found
            return FieldClassification(
                field_class=field_class,
                confidence=0.80,
                evidence=(f"context:{evidence}",),
                key=key,
            )

    return FieldClassification(
        field_class="unknown", confidence=0.0, evidence=("no_signal",)
    )
