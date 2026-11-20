from __future__ import annotations

from pathlib import Path

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.resume import ParsedResume, ResumeFileRef
from app.domain.ports.resume import ResumeParser

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 20
MIN_TEXT_CHARS = 50


class PdfResumeParser(ResumeParser):
    id = "resume.parser.pdf"
    supported_mime_types = frozenset({"application/pdf"})

    async def parse(self, file: ResumeFileRef) -> Result[ParsedResume, PortError]:
        path = Path(file.path)
        try:
            size = path.stat().st_size
        except OSError as exc:
            return Err(PortError(code="resume.read_failed", message=str(exc), retryable=False))
        if size > MAX_BYTES:
            return Err(PortError(code="resume.too_large", message=f"file exceeds {MAX_BYTES} bytes", retryable=False))
        try:
            import fitz  # type: ignore[import-untyped]
        except ImportError as exc:
            return Err(PortError(code="resume.missing_dep", message=str(exc), retryable=False))
        try:
            doc = fitz.open(str(path))
        except Exception as exc:  # noqa: BLE001
            return Err(PortError(code="resume.parse_failed", message=str(exc), retryable=False))
        if doc.is_encrypted:
            try:
                if not doc.authenticate(""):
                    doc.close()
                    return Err(PortError(code="resume.encrypted", message="PDF is password-protected", retryable=False))
            except Exception:  # noqa: BLE001
                doc.close()
                return Err(PortError(code="resume.encrypted", message="PDF is password-protected", retryable=False))
        if doc.page_count > MAX_PAGES:
            doc.close()
            return Err(PortError(code="resume.too_many_pages", message=f"exceeds {MAX_PAGES} pages", retryable=False))
        # Layout-aware: use blocks sorted by (column, then y) to handle
        # two-column resumes better than naive get_text().
        parts: list[str] = []
        for page in doc:
            blocks = page.get_text("blocks")
            # blocks: (x0, y0, x1, y1, text, block_no, block_type)
            # sort by column (x0 bucket) then y
            mid = page.rect.width / 2
            left = [b for b in blocks if b[0] < mid and b[6] == 0]
            right = [b for b in blocks if b[0] >= mid and b[6] == 0]
            for col in (left, right):
                col.sort(key=lambda b: (round(b[1] / 20), b[0]))
                for b in col:
                    txt = b[4].strip()
                    if txt:
                        parts.append(txt)
        doc.close()
        text = "\n".join(parts)
        if len(text.strip()) < MIN_TEXT_CHARS:
            return Err(PortError(code="resume.no_text", message="no extractable text (scanned/image PDF?)", retryable=False))
        return Ok(ParsedResume(text=text, parser_id=self.id, parser_version="1.2"))
