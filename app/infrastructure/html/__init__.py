"""HTML adapters (M2)."""

from app.infrastructure.html.parsing import (
    collect,
    headings,
    links,
    meta_tags,
    plain_text,
    scripts,
    text_blocks,
)

__all__ = [
    "collect",
    "headings",
    "links",
    "meta_tags",
    "plain_text",
    "scripts",
    "text_blocks",
]
