"""HTML adapters (M2)."""

from app.infrastructure.html.parsing import (
    collect,
    headings,
    meta_tags,
    plain_text,
    scripts,
    text_blocks,
)

__all__ = [
    "collect",
    "headings",
    "meta_tags",
    "plain_text",
    "scripts",
    "text_blocks",
]
