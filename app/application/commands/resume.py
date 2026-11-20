"""Resume import and profile management (M5)."""
from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from app.application.container import Container
from app.domain.models.resume import ParsedResume, ResumeFileRef
from app.domain.services.keywords import KeywordExtractor
from app.domain.services.sectioner import section_text
from app.plugins.resume.docx import DocxResumeParser
from app.plugins.resume.pdf import PdfResumeParser
from app.plugins.resume.txt import TxtResumeParser

_PARSERS = [TxtResumeParser(), PdfResumeParser(), DocxResumeParser()]

_MIME_BY_EXT = {
    ".txt": "text/plain",
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


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
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        # content-addressed copy
        dest = self._resumes_dir / sha[:2] / sha
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copyfile(path, dest)

        parser = self._parser_for(mime)
        if parser is None:
            raise ValueError(f"no parser for {mime}")
        ref = ResumeFileRef(path=str(dest), mime_type=mime, original_name=path.name, sha256=sha)
        result = await parser.parse(ref)
        from app.domain.models.common import Ok
        if not isinstance(result, Ok):
            raise ValueError(str(result.error.message))
        parsed: ParsedResume = result.value

        # create profile if none exists (reuse a default profile)
        profile_id = "default"
        async with self._container.uow_factory() as uow:
            prof = await uow.resumes.get_profile(profile_id)
            if prof is None:
                await uow.resumes.add_profile(profile_id, "Default")
            # resume file row
            file_id = f"rf_{uuid.uuid4().hex[:12]}"
            await uow.resumes.add_resume_file(file_id, sha, path.name, mime, len(raw), str(dest))
            # skills
            skills = KeywordExtractor().extract(parsed.text)
            # create version
            version_id = f"rv_{uuid.uuid4().hex[:12]}"
            sections = section_text(parsed.text)
            parsed_json = json.dumps({"text": parsed.text, "sections": sections, "parser_id": parsed.parser_id})
            await uow.resumes.add_resume_version(
                version_id, profile_id, file_id, label, parsed.parser_id, parsed.parser_version,
                hashlib.sha256(parsed.text.encode()).hexdigest(), parsed_json, is_active=True,
            )
            await uow.resumes.save_skills(version_id, [(s, "explicit", 0.8) for s in skills])
            await uow.resumes.set_active(profile_id, version_id)
            await uow.commit()

        return {"profile_id": profile_id, "version_id": version_id, "skills": skills, "sections": list(sections.keys())}

    async def list_versions(self, profile_id: str = "default") -> list[dict[str, Any]]:
        async with self._container.uow_factory() as uow:
            rows = await uow.resumes.list_versions(profile_id)
            await uow.commit()
        return [dict(r) for r in rows]

    async def get_profile(self, profile_id: str = "default") -> dict[str, Any] | None:
        async with self._container.uow_factory() as uow:
            p = await uow.resumes.get_profile(profile_id)
            await uow.commit()
        return dict(p) if p else None

    async def set_active(self, profile_id: str, version_id: str) -> None:
        async with self._container.uow_factory() as uow:
            await uow.resumes.set_active(profile_id, version_id)
            await uow.commit()

    async def update_overrides(self, profile_id: str, overrides: dict[str, Any]) -> None:
        async with self._container.uow_factory() as uow:
            await uow.resumes.update_overrides(profile_id, overrides)
            await uow.commit()
