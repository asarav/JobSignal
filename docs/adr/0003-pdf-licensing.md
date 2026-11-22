# ADR 0003: PDF parsing licensing (PyMuPDF is AGPL or commercial)

- **Status:** Accepted — release gate, not yet cleared for distribution
- **Date:** 2026-11-21
- **Related:** spec §10.1 (resume import), `app/plugins/resume/pdf.py`, `pyproject.toml`

## Context

Resume import (Milestone 5) must read PDF, DOCX and TXT files locally. The
shipped PDF implementation uses **PyMuPDF** (`pip install pymupdf`, imported as
`fitz`) for layout-aware block extraction. PyMuPDF is dual-licensed:

- **GNU AGPL v3**, which requires the whole combined work — including the
  desktop application distributed to users — to be licensed under the AGPL and
  to offer corresponding source, or
- a **commercial licence from Artifex**.

`pymupdf` is currently a *required* dependency in `pyproject.toml`, so every
build of Job Signal links AGPL code. That is a real distribution obligation,
not a documentation detail: shipping a proprietary installer while depending on
PyMuPDF is a licence violation.

## Decision

1. **Record the obligation now and gate distribution on it.** No public binary
   release may be produced until one of the following is true:
   - the project is released under AGPL-3.0-compatible terms (source offered,
     notice included), or
   - a commercial PyMuPDF licence is obtained and recorded here, or
   - the default PDF parser is replaced (see option 3 below).
2. **Keep the parser behind the `ResumeParser` port.** `PdfResumeParser` is a
   plugin (`id = "resume.parser.pdf"`), selected by MIME type in
   `ResumeUseCase._parser_for`; swapping the implementation does not touch the
   application layer, persistence or IPC.
3. **Preferred long-term replacement:** a permissively licensed parser
   (`pypdf`, MIT) covers text extraction for the resume use case. Layout-aware
   block ordering is the only feature that currently justifies PyMuPDF; if
   `pypdf` output proves adequate on the golden fixtures, make it the default
   and move PyMuPDF to an optional `pdf-layout` extra that users install
   themselves (in which case the AGPL obligation follows *their* installation,
   not the distributed build).
4. **No silent fallback.** If the PDF dependency is missing, the parser returns
   `resume.missing_dep`; it never pretends a file was parsed.

## Options considered

- **Ship AGPL and comply** — simplest technically; forces the whole application
  to AGPL. Viable only if the product is meant to be open source.
- **Buy the Artifex commercial licence** — removes the copyleft obligation but
  adds a recurring cost and a per-seat/redistribution contract to track.
- **Replace with `pypdf`** — MIT, no obligation, weaker layout handling on
  two-column resumes; the plugin boundary makes this a contained change.
- **Ignore it** — rejected: the dependency is already in the shipped build, so
  this is a legal defect rather than a deferred decision.

## Consequences

- The repository can keep using PyMuPDF for local development and testing.
- Any packaging/release checklist must include this ADR; CI cannot detect the
  licence conflict because it is a distribution question, not a build failure.
- Until the gate is cleared, README and release notes must not present binary
  builds as distributable.
- If the parser is swapped, the golden resume fixtures remain the acceptance
  test for text quality and sectioning.
