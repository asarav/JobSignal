from __future__ import annotations

from pathlib import Path

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.resume import ParsedResume, ResumeFileRef
from app.domain.ports.resume import ResumeParser

MAX_BYTES = 10 * 1024 * 1024


class DocxResumeParser(ResumeParser):
    id = "resume.parser.docx"
    supported_mime_types = frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    )

    async def parse(self, file: ResumeFileRef) -> Result[ParsedResume, PortError]:
        path = Path(file.path)
        try:
            size = path.stat().st_size
        except OSError as exc:
            return Err(PortError(code="resume.read_failed", message=str(exc), retryable=False))
        if size > MAX_BYTES:
            return Err(PortError(code="resume.too_large", message=f"file exceeds {MAX_BYTES} bytes", retryable=False))
        try:
            import docx
        except ImportError as exc:
            return Err(PortError(code="resume.missing_dep", message=str(exc), retryable=False))
        try:
            document = docx.Document(str(path))
        except Exception as exc:  # noqa: BLE001
            return Err(PortError(code="resume.parse_failed", message=str(exc), retryable=False))
        parts: list[str] = []
        for p in document.paragraphs:
            if p.text.strip():
                parts.append(p.text)
        # tables
        for table in document.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        text = "\n".join(parts)
        if not text.strip():
            return Err(PortError(code="resume.empty", message="no text content", retryable=False))
        return Ok(ParsedResume(text=text, parser_id=self.id, parser_version="1.2"))
