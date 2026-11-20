from __future__ import annotations

from pathlib import Path

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.resume import ParsedResume, ResumeFileRef
from app.domain.ports.resume import ResumeParser

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 20


class TxtResumeParser(ResumeParser):
    id = "resume.parser.txt"
    supported_mime_types = frozenset({"text/plain"})

    async def parse(self, file: ResumeFileRef) -> Result[ParsedResume, PortError]:
        path = Path(file.path)
        try:
            size = path.stat().st_size
        except OSError as exc:
            return Err(PortError(code="resume.read_failed", message=str(exc), retryable=False))
        if size > MAX_BYTES:
            return Err(PortError(code="resume.too_large", message=f"file exceeds {MAX_BYTES} bytes", retryable=False))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return Err(PortError(code="resume.read_failed", message=str(exc), retryable=False))
        if not text.strip():
            return Err(PortError(code="resume.empty", message="no text content", retryable=False))
        return Ok(ParsedResume(text=text, parser_id=self.id, parser_version="1.1"))
