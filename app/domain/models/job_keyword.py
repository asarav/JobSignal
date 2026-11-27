"""Per-posting keyword records (M9).

Each extraction is scoped to the posting revision it read, so a materially
updated repost adds a revision rather than overwriting the earlier one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class JobKeywordRevisionRecord:
    id: str
    job_id: str
    revision_hash: str
    taxonomy_version: str
    extracted_at: datetime
    keyword_count: int = 0


@dataclass(frozen=True, slots=True)
class JobKeywordRecord:
    id: str
    job_id: str
    revision_hash: str
    keyword_id: str
    taxonomy_version: str
    extracted_at: datetime
    mentions: int = 1
    evidence: Mapping[str, object] = field(default_factory=dict)
    #: ``required`` / ``preferred`` / ``mentioned``; section-aware.
    importance: str = "mentioned"
