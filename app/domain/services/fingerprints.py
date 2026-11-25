"""Fingerprints for job identity and dedup (spec §7.2).

Pure functions: no I/O, no third-party dependencies.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence


def canonical_url(url: str) -> str:
    """Lowercase host, strip fragments/tracking params/trailing slash."""
    url = url.split("#", 1)[0]
    # strip common tracking params
    if "?" in url:
        base, _, query = url.partition("?")
        params = [
            p
            for p in query.split("&")
            if p and not p.lower().startswith(("utm_", "ref=", "src=", "source="))
        ]
        url = base + ("?" + "&".join(params) if params else "")
    return url.rstrip("/").lower()


def url_key(url: str) -> str:
    return canonical_url(url)


def ats_key(
    ats_id: str | None, tenant_key: str | None, source_posting_id: str | None
) -> str | None:
    if not (ats_id and tenant_key and source_posting_id):
        return None
    return f"{ats_id}|{tenant_key}|{source_posting_id}"


def title_key(normalized_title: str, level: int | None = None) -> str:
    t = (normalized_title or "").strip().lower()
    if level is not None:
        t = f"{t}|lvl{level}"
    return t


def location_key(locations: Sequence[str] | None, workplace_type: str | None = None) -> str | None:
    if not locations and not workplace_type:
        return None
    parts = sorted({(loc or "").strip().lower() for loc in (locations or []) if loc})
    return "|".join(parts) + f"|{workplace_type or 'unknown'}"


def normalize_text(text: str) -> str:
    text = text or ""
    text = re.sub(r"\s+", " ", text)
    return text.strip().lower()


def desc_exact_hash(description: str) -> str:
    return hashlib.sha256(normalize_text(description).encode("utf-8")).hexdigest()


def _shingles(text: str, k: int = 3) -> list[str]:
    words = normalize_text(text).split()
    if len(words) < k:
        return [" ".join(words)] if words else []
    return [" ".join(words[i : i + k]) for i in range(len(words) - k + 1)]


def simhash64(text: str, k: int = 3) -> int:
    """64-bit simhash over word k-shingles."""
    shingles = _shingles(text, k)
    if not shingles:
        return 0
    v = [0] * 64
    for sh in shingles:
        h = int(hashlib.md5(sh.encode("utf-8")).hexdigest(), 16)
        for i in range(64):
            if (h >> i) & 1:
                v[i] += 1
            else:
                v[i] -= 1
    result = 0
    for i in range(64):
        if v[i] > 0:
            result |= 1 << i
    return result


def to_signed64(value: int | None) -> int | None:
    """Map an unsigned 64-bit hash into SQLite's signed INTEGER range."""
    if value is None:
        return None
    return value - (1 << 64) if value >= (1 << 63) else value


def from_signed64(value: int | None) -> int | None:
    """Restore the unsigned 64-bit form used by fingerprint comparisons."""
    if value is None:
        return None
    return value + (1 << 64) if value < 0 else value


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def minhash_signature(text: str, num_perm: int = 64, k: int = 5) -> list[int]:
    """MinHash signature over word k-shingles using multiple hash seeds."""
    shingles = set(_shingles(text, k))
    if not shingles:
        return [0] * num_perm
    sig: list[int] = []
    for seed in range(num_perm):
        min_val = None
        for sh in shingles:
            h = int(hashlib.md5((str(seed) + sh).encode("utf-8")).hexdigest(), 16)
            if min_val is None or h < min_val:
                min_val = h
        sig.append(min_val if min_val is not None else 0)
    return sig


def jaccard_est(sig_a: Sequence[int], sig_b: Sequence[int]) -> float:
    if not sig_a or not sig_b or len(sig_a) != len(sig_b):
        return 0.0
    matches = sum(1 for a, b in zip(sig_a, sig_b, strict=False) if a == b)
    return matches / len(sig_a)


def remove_boilerplate(descriptions: Sequence[str]) -> list[str]:
    """Strip paragraphs appearing in >=60% of >=5 jobs of the same company."""
    if len(descriptions) < 5:
        return list(descriptions)
    # paragraph-level
    para_counts: dict[str, int] = {}
    parsed: list[list[str]] = []
    for d in descriptions:
        paras = [p.strip() for p in (d or "").split("\n\n") if p.strip()]
        parsed.append(paras)
        for p in set(paras):
            para_counts[p] = para_counts.get(p, 0) + 1
    threshold = max(5, int(0.6 * len(descriptions)))
    boiler = {p for p, c in para_counts.items() if c >= threshold}
    out: list[str] = []
    for paras in parsed:
        kept = [p for p in paras if p not in boiler]
        out.append("\n\n".join(kept))
    return out
