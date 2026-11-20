from __future__ import annotations

from pathlib import Path

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.resume import ParsedResume, ResumeFileRef
from app.domain.ports.resume import ResumeParser


class TxtResumeParser(ResumeParser):
    id = "resume.parser.txt"
    supported_mime_types = frozenset({"text/plain"})

    async def parse(self, file: ResumeFileRef) -> Result[ParsedResume, PortError]:
        try:
            text = Path(file.path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return Err(PortError(code="resume.read_failed", message=str(exc), retryable=False))
        return Ok(ParsedResume(text=text, parser_id=self.id, parser_version="1.0"))
