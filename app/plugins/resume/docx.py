from __future__ import annotations

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.resume import ParsedResume, ResumeFileRef
from app.domain.ports.resume import ResumeParser


class DocxResumeParser(ResumeParser):
    id = "resume.parser.docx"
    supported_mime_types = frozenset(
        {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
    )

    async def parse(self, file: ResumeFileRef) -> Result[ParsedResume, PortError]:
        try:
            import docx
        except ImportError as exc:
            return Err(PortError(code="resume.missing_dep", message=str(exc), retryable=False))
        try:
            document = docx.Document(file.path)
            text = "\n".join(p.text for p in document.paragraphs)
        except Exception as exc:  # noqa: BLE001
            return Err(PortError(code="resume.parse_failed", message=str(exc), retryable=False))
        return Ok(ParsedResume(text=text, parser_id=self.id, parser_version="1.0"))
