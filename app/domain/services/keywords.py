"""Keyword extraction and normalization (spec sec 12)."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_TAXONOMY_PATH = Path(__file__).resolve().parents[3] / "data" / "taxonomy" / "v1.json"


def load_taxonomy(path: Path | None = None) -> dict[str, Any]:
    p = path or _TAXONOMY_PATH
    return json.loads(p.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def normalize_token(tok: str) -> str:
    return tok.strip().lower()


class KeywordNormalizer:
    def __init__(self, taxonomy: dict[str, Any] | None = None) -> None:
        self.taxonomy: dict[str, Any] = taxonomy or load_taxonomy()
        self._by_id: dict[str, dict[str, Any]] = {s["id"]: s for s in self.taxonomy["skills"]}
        self._alias_map: dict[str, str] = {}
        for s in self.taxonomy["skills"]:
            self._alias_map[s["canonical"].lower()] = s["id"]
            for a in s.get("aliases", []):
                self._alias_map[a.lower()] = s["id"]

    def normalize(self, mention: str) -> str | None:
        key = mention.strip().lower()
        return self._alias_map.get(key)

    def taxonomy_version(self) -> str:
        return str(self.taxonomy.get("version", "taxonomy.v1"))

    def implications(self) -> list[tuple[str, str, float]]:
        out: list[tuple[str, str, float]] = []
        for i in self.taxonomy.get("implications", []):
            out.append((str(i["from"]), str(i["to"]), float(i["strength"])))
        return out


class KeywordExtractor:
    def __init__(self, normalizer: KeywordNormalizer | None = None) -> None:
        self.normalizer = normalizer or KeywordNormalizer()

    def extract(self, text: str) -> list[str]:
        text_lower = (text or "").lower()
        found: set[str] = set()
        for alias, sid in self.normalizer._alias_map.items():
            # word-boundary-ish match
            pattern = r"(?<![a-z0-9])" + re.escape(alias) + r"(?![a-z0-9])"
            if re.search(pattern, text_lower):
                found.add(sid)
        # implications
        for frm, to, strength in self.normalizer.implications():
            if frm in found and strength >= 0.5:
                found.add(to)
        return sorted(found)
