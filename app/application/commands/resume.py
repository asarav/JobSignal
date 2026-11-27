"""Resume import and profile management (M5)."""
from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.application.container import Container
from app.domain.models.resume import ParsedResume, ResumeFileRef
from app.domain.services.keywords import KeywordExtractor, normalize_skill_ids
from app.domain.services.sectioner import (
    compute_skill_tenure,
    compute_years_experience,
    extract_contact,
    extract_education_entries,
    extract_experience_entries,
    extract_profile_items,
    parse_experience_header,
    section_text,
)
from app.plugins.resume.docx import DocxResumeParser
from app.plugins.resume.pdf import PdfResumeParser
from app.plugins.resume.txt import TxtResumeParser

_PARSERS = [TxtResumeParser(), PdfResumeParser(), DocxResumeParser()]

_MIME_BY_EXT = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _evidence_refs(
    extractor: KeywordExtractor, sections: Mapping[str, str], skill: str
) -> list[dict[str, Any]]:
    """Character spans backing a skill, per section (spec sec 10.3)."""
    refs: list[dict[str, Any]] = []
    for section, text in sections.items():
        for keyword_id, start, end in extractor.mentions_with_spans(text):
            if keyword_id == skill:
                refs.append({"section": section, "start": start, "end": end})
    return refs[:8]


def _skill_evidence(
    extractor: KeywordExtractor,
    skills: list[str],
    sections: Mapping[str, str],
    entries: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Attach spec sec 10.3 evidence levels and tenure to extracted skills.

    ``explicit_with_experience`` requires the skill to be listed in Skills *and*
    written in the experience/project text; ``inferred`` covers taxonomy
    implications and summary-only mentions; ``limited`` is a single weak mention.
    """
    listed = set(extractor.mentions(sections.get("skills", "")))
    experience_text = "\n".join(
        part for part in (sections.get("experience", ""), sections.get("projects", "")) if part
    )
    in_experience = set(extractor.mentions(experience_text))
    in_summary = set(extractor.mentions(sections.get("summary", "")))
    direct = set(extractor.mentions("\n".join(sections.values())))
    out: list[dict[str, Any]] = []
    for skill in skills:
        if skill not in direct:
            evidence, confidence = "inferred", 0.55  # implied by the taxonomy only
        elif skill in listed and skill in in_experience:
            evidence, confidence = "explicit_with_experience", 0.9
        elif skill in listed:
            evidence, confidence = "explicit", 0.8
        elif skill in in_experience:
            evidence, confidence = "explicit", 0.7  # used in a role, absent from Skills
        elif skill in in_summary:
            evidence, confidence = "inferred", 0.6  # summary-only mention
        else:
            evidence, confidence = "limited", 0.4
        duration, recency = compute_skill_tenure(entries, skill, extractor.mentions)
        out.append(
            {
                "keyword_id": skill,
                "evidence": evidence,
                "confidence": confidence,
                "evidence_refs": _evidence_refs(extractor, sections, skill),
                "duration_years": duration,
                "recency_years": recency,
            }
        )
    return out


class ResumeUseCase:
    def __init__(self, container: Container, data_dir: Path) -> None:
        self._container = container
        self._data_dir = data_dir
        self._resumes_dir = data_dir / "resumes"
        self._resumes_dir.mkdir(parents=True, exist_ok=True)

    def _parser_for(self, mime: str) -> Any:
        for p in _PARSERS:
            if mime in p.supported_mime_types:
                return p
        return None

    async def import_resume(self, file_path: str, label: str = "Imported") -> dict[str, Any]:
        path = Path(file_path)
        mime = _MIME_BY_EXT.get(path.suffix.lower(), "text/plain")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            from app.domain.models.common import PortError
            raise PortError(code="resume.read_failed", message=str(exc), retryable=False) from exc
        sha = hashlib.sha256(raw).hexdigest()
        # content-addressed copy
        dest = self._resumes_dir / sha[:2] / sha
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copyfile(path, dest)

        parser = self._parser_for(mime)
        if parser is None:
            from app.domain.models.common import PortError
            raise PortError(code="resume.no_parser", message=f"no parser for {mime}", retryable=False)
        ref = ResumeFileRef(path=str(dest), mime_type=mime, original_name=path.name, sha256=sha)
        result = await parser.parse(ref)
        from app.domain.models.common import Ok
        if not isinstance(result, Ok):
            from app.domain.models.common import PortError
            raise PortError(code=result.error.code, message=result.error.message, retryable=False)
        parsed: ParsedResume = result.value

        # create profile if none exists (reuse a default profile)
        profile_id = "default"
        async with self._container.uow_factory() as uow:
            prof = await uow.resumes.get_profile(profile_id)
            if prof is None:
                await uow.resumes.add_profile(profile_id, "Default")
            previous_version_id = (
                str(prof.get("active_resume_version_id"))
                if prof and prof.get("active_resume_version_id")
                else None
            )
            # One resume_files row per content hash: importing the same bytes
            # again reuses it instead of violating the sha256 UNIQUE constraint.
            existing_file = await uow.resumes.get_resume_file_by_sha256(sha)
            if existing_file is None:
                file_id = f"rf_{uuid.uuid4().hex[:12]}"
                await uow.resumes.add_resume_file(file_id, sha, path.name, mime, len(raw), str(dest))
            else:
                file_id = str(existing_file["id"])
            # create version
            version_id = f"rv_{uuid.uuid4().hex[:12]}"
            sections = section_text(parsed.text)
            entries = extract_experience_entries(sections.get("experience", ""))
            for entry in entries:
                employer, title = parse_experience_header(entry.get("header", ""))
                entry["employer"] = employer or ""
                entry["title"] = title or ""
                entry["confidence"] = "0.8" if employer and title else "0.4"
            years = compute_years_experience(entries)
            education = extract_education_entries(sections.get("education", ""))
            certifications = extract_profile_items(sections.get("certifications", ""))
            projects = extract_profile_items(sections.get("projects", ""))
            contact = extract_contact(parsed.text)
            extractor = KeywordExtractor()
            skills = extractor.extract(parsed.text)
            evidence_skills = _skill_evidence(extractor, skills, sections, entries)
            parsed_json = json.dumps({
                "text": parsed.text,
                "sections": sections,
                "parser_id": parsed.parser_id,
                "experience": entries,
                "education": education,
                "certifications": certifications,
                "projects": projects,
                "contact": contact,
                "years_experience": years,
            })
            await uow.resumes.add_resume_version(
                version_id, profile_id, file_id, label, parsed.parser_id, parsed.parser_version,
                hashlib.sha256(parsed.text.encode()).hexdigest(), parsed_json,
                parent_version_id=previous_version_id, is_active=True,
            )
            await uow.resumes.save_skills(version_id, evidence_skills)
            await uow.resumes.save_experience(version_id, entries)
            await uow.resumes.save_education(version_id, education)
            await uow.resumes.save_profile_items(version_id, "certification", certifications)
            await uow.resumes.save_profile_items(version_id, "project", projects)
            await uow.resumes.update_contact(profile_id, contact)
            # Parser-inferred values never overwrite a manual override (spec sec 10.4).
            overrides = dict(await uow.resumes.get_overrides(profile_id))
            if years is not None:
                overrides.setdefault("years_experience_total", years)
            await uow.resumes.update_overrides(profile_id, overrides)
            await uow.resumes.set_active(profile_id, version_id)
            await uow.commit()

        return {
            "profile_id": profile_id,
            "version_id": version_id,
            "skills": skills,
            "sections": list(sections.keys()),
            "contact": contact,
            "years_experience": years,
            "experience": len(entries),
            "education": len(education),
            "certifications": len(certifications),
            "projects": len(projects),
        }

    async def list_versions(self, profile_id: str = "default") -> list[dict[str, Any]]:
        async with self._container.uow_factory() as uow:
            rows = await uow.resumes.list_versions(profile_id)
            await uow.commit()
        root = self._resumes_dir.resolve()
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            stored = item.get("stored_path")
            if stored is not None:
                # Only expose copies inside the content-addressed store.
                try:
                    Path(str(stored)).resolve().relative_to(root)
                except (OSError, ValueError):
                    item.pop("stored_path", None)
            out.append(item)
        return out

    async def get_profile(self, profile_id: str = "default") -> dict[str, Any] | None:
        async with self._container.uow_factory() as uow:
            profile = await uow.resumes.get_profile(profile_id)
            if profile is None:
                await uow.commit()
                return None
            result = dict(profile)
            version_id = result.get("active_resume_version_id")
            overrides = _json_object(result.get("overrides_json"))
            contact = _json_object(result.get("contact_json"))
            override_contact = overrides.get("contact")
            if isinstance(override_contact, dict):
                contact.update(override_contact)
            result["contact"] = contact
            skills: list[dict[str, Any]] = []
            experience: list[dict[str, Any]] = []
            education: list[dict[str, Any]] = []
            certifications: list[dict[str, Any]] = []
            projects: list[dict[str, Any]] = []
            if version_id:
                statuses = await uow.resumes.get_skill_status(profile_id)
                for row in await uow.resumes.get_skills(str(version_id)):
                    skills.append(
                        {
                            "keyword_id": row["keyword_id"],
                            "evidence": row["evidence"],
                            "confidence": row["confidence"],
                            "duration_years": row.get("duration_years"),
                            "recency_years": row.get("recency_years"),
                            "evidence_refs": _json_list(row.get("evidence_refs_json")),
                            "status": statuses.get(str(row["keyword_id"])),
                        }
                    )
                known = {str(row["keyword_id"]) for row in skills}
                for key in normalize_skill_ids(overrides.get("skills_added")):
                    if key in known:
                        continue
                    skills.append(
                        {
                            "keyword_id": key,
                            "evidence": "explicit",
                            "confidence": 0.8,
                            "duration_years": None,
                            "recency_years": None,
                            "evidence_refs": [],
                            "status": statuses.get(key),
                            "source": "user",
                        }
                    )
                experience = [dict(row) for row in await uow.resumes.get_experience(str(version_id))]
                education = [dict(row) for row in await uow.resumes.get_education(str(version_id))]
                certifications = [
                    dict(row)
                    for row in await uow.resumes.get_profile_items(str(version_id), "certification")
                ]
                projects = [
                    dict(row)
                    for row in await uow.resumes.get_profile_items(str(version_id), "project")
                ]
            # Manual corrections sit on top of parser output (spec sec 10.4).
            result["experience"] = _override_list(overrides, "experience", experience)
            result["education"] = _override_list(overrides, "education", education)
            result["certifications"] = _override_list(
                overrides, "certifications", certifications
            )
            result["projects"] = _override_list(overrides, "projects", projects)
            result["skills"] = skills
            await uow.commit()
        return result

    async def set_skill_status(
        self, profile_id: str, keyword_id: str, status: str
    ) -> None:
        async with self._container.uow_factory() as uow:
            await uow.resumes.set_skill_status(profile_id, keyword_id, status)
            await uow.commit()

    async def parse_version(self, version_id: str) -> dict[str, Any]:
        """Re-run the parser on a stored version without changing it."""
        from app.domain.models.common import Ok, PortError

        async with self._container.uow_factory() as uow:
            version = await uow.resumes.get_version(version_id)
            if version is None:
                raise PortError(
                    code="resume.not_found", message="resume version not found", retryable=False
                )
            stored = await uow.resumes.get_resume_file(str(version["resume_file_id"]))
            await uow.commit()
        if stored is None:
            raise PortError(
                code="resume.not_found", message="resume file not found", retryable=False
            )
        path = Path(str(stored["stored_path"]))
        parser = self._parser_for(str(stored.get("mime_type") or "text/plain"))
        if parser is None:
            raise PortError(
                code="resume.no_parser", message="no parser for this file", retryable=False
            )
        ref = ResumeFileRef(
            path=str(path),
            mime_type=str(stored.get("mime_type") or "text/plain"),
            original_name=str(stored.get("original_name") or path.name),
            sha256=str(stored.get("sha256") or ""),
        )
        result = await parser.parse(ref)
        if not isinstance(result, Ok):
            raise PortError(
                code=result.error.code,
                message=result.error.message,
                retryable=False,
            )
        parsed: ParsedResume = result.value
        sections = section_text(parsed.text)
        entries = extract_experience_entries(sections.get("experience", ""))
        extractor = KeywordExtractor()
        return {
            "version_id": version_id,
            "text": parsed.text,
            "sections": sections,
            "skills": extractor.extract(parsed.text),
            "years_experience": compute_years_experience(entries),
        }

    async def set_active(self, profile_id: str, version_id: str) -> None:
        async with self._container.uow_factory() as uow:
            await uow.resumes.set_active(profile_id, version_id)
            await uow.commit()

    async def update_overrides(self, profile_id: str, overrides: dict[str, Any]) -> None:
        preferences = overrides.get("preferences")
        if isinstance(preferences, dict):
            locations = preferences.get("locations")
            if not preferences.get("home_location") and isinstance(locations, list) and locations:
                preferences["home_location"] = str(locations[0])
            if not preferences.get("authorized_countries"):
                from app.domain.services.location_scope import canonical_country

                country = canonical_country(str(preferences.get("home_location") or ""))
                preferences["authorized_countries"] = [country or "United States"]
        async with self._container.uow_factory() as uow:
            await uow.resumes.update_overrides(profile_id, overrides)
            await uow.commit()


def _json_object(value: object) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: object) -> list[Any]:
    if not isinstance(value, str) or not value:
        return []
    try:
        parsed = json.loads(value)
    except ValueError:
        return []
    return parsed if isinstance(parsed, list) else []


def _override_list(
    overrides: Mapping[str, Any], key: str, parsed: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    value = overrides.get(key)
    if isinstance(value, list) and all(isinstance(item, dict) for item in value):
        return [dict(item) for item in value]
    return parsed
