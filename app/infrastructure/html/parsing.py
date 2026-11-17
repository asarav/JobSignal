"""Small HTML helpers built on the standard library.

No third-party parser is used: the extraction strategies only need scripts,
meta tags, headings and text blocks, and ``html.parser`` covers those without
adding a dependency (see ADR-0002 and §22.7 "avoid unnecessary dependencies").
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html import unescape
from html.parser import HTMLParser

# Text-bearing elements we collect as candidate blocks.
BLOCK_TAGS = frozenset({"p", "li", "div", "section", "article", "td", "dd"})


@dataclass
class ScriptBlock:
    """A ``<script>`` element with its attributes and text."""

    attrs: dict[str, str] = field(default_factory=dict)
    text: str = ""


@dataclass
class HeadingBlock:
    """A heading element (``h1``-``h6``) with its level and text."""

    level: int
    text: str


@dataclass
class LinkBlock:
    """An anchor with its href and its text."""

    href: str
    text: str


class _Collector(HTMLParser):
    """Collects scripts, meta tags, headings, links and text blocks in one pass."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[ScriptBlock] = []
        self.meta: dict[str, str] = {}
        self.headings: list[HeadingBlock] = []
        self.links: list[LinkBlock] = []
        self.blocks: list[str] = []
        self._script: ScriptBlock | None = None
        self._heading: HeadingBlock | None = None
        self._link: LinkBlock | None = None
        self._text: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: (value or "") for key, value in attrs}
        if tag in {"script", "style"}:
            if tag == "script":
                self._script = ScriptBlock(attrs=attributes)
            self._skip_depth += 1
            return
        if tag == "meta":
            name = attributes.get("name") or attributes.get("property")
            content = attributes.get("content")
            if name and content:
                self.meta.setdefault(name.lower(), content)
            return
        if tag == "a":
            self._link = LinkBlock(href=attributes.get("href", ""), text="")
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            self._heading = HeadingBlock(level=int(tag[1]), text="")
            return
        if tag in BLOCK_TAGS:
            self._flush_text()

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._skip_depth = max(0, self._skip_depth - 1)
            if tag == "script" and self._script is not None:
                self.scripts.append(self._script)
                self._script = None
            return
        if tag == "a":
            if self._link is not None:
                if self._link.text.strip() or self._link.href:
                    self.links.append(self._link)
                self._link = None
            return
        if tag in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            if self._heading is not None and self._heading.text.strip():
                self.headings.append(self._heading)
            self._heading = None
            return
        if tag in BLOCK_TAGS:
            self._flush_text()

    def handle_data(self, data: str) -> None:
        if self._script is not None:
            self._script.text += data
            return
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._heading is not None:
            self._heading.text = f"{self._heading.text} {text}".strip()
            return
        if self._link is not None:
            self._link.text = f"{self._link.text} {text}".strip()
            return
        self._text.append(text)

    def close(self) -> None:  # type: ignore[override]
        super().close()
        self._flush_text()

    def _flush_text(self) -> None:
        if self._text:
            self.blocks.append(" ".join(self._text))
            self._text = []


def collect(html: str) -> _Collector:
    """Parse ``html`` and return the collected scripts, meta tags and text."""
    collector = _Collector()
    try:
        collector.feed(html)
        collector.close()
    except Exception:  # noqa: BLE001 - malformed HTML must not stop extraction
        pass
    return collector


def scripts(html: str) -> list[ScriptBlock]:
    return collect(html).scripts


def meta_tags(html: str) -> dict[str, str]:
    return collect(html).meta


def headings(html: str) -> list[HeadingBlock]:
    return collect(html).headings


def links(html: str) -> list[LinkBlock]:
    return collect(html).links


def text_blocks(html: str) -> list[str]:
    return collect(html).blocks


def plain_text(html: str) -> str:
    """All visible text of the document, whitespace-collapsed."""
    return unescape(" ".join(text_blocks(html))).strip()
