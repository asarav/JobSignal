from __future__ import annotations

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.resume import ParsedResume, ResumeFileRef
from app.domain.ports.resume import ResumeParser


class PdfResumeParser(ResumeParser):
    id = "resume.parser.pdf"
    supported_mime_types = frozenset({"application/pdf"})

    async def parse(self, file: ResumeFileRef) -> Result[ParsedResume, PortError]:
        try:
            import fitz  # type: ignore[import-untyped]
        except ImportError as exc:
            return Err(PortError(code="resume.missing_dep", message=str(exc), retryable=False))
        try:
            doc = fitz.open(file.path)
            text = "\n".join(page.get_text() for page in doc)
            doc.close()
        except Exception as exc:  # noqa: BLE001
            return Err(PortError(code="resume.parse_failed", message=str(exc), retryable=False))
        if len(text.strip()) < 50:
            return Err(PortError(code="resume.no_text", message="no extractable text", retryable=False))
        return Ok(ParsedResume(text=text, parser_id=self.id, parser_version="1.0"))
