"""Unit tests for fingerprints."""
from __future__ import annotations

from app.domain.services.fingerprints import (
    ats_key,
    canonical_url,
    desc_exact_hash,
    hamming,
    jaccard_est,
    location_key,
    minhash_signature,
    simhash64,
    title_key,
    url_key,
)


def test_canonical_url_strips_tracking():
    u = "https://example.com/jobs/1?utm_source=x&ref=y"
    assert canonical_url(u) == "https://example.com/jobs/1"


def test_url_key_lowercases():
    assert url_key("https://Example.com/Jobs/1/") == "https://example.com/jobs/1"


def test_ats_key():
    assert ats_key("ats.greenhouse", "acme", "1") == "ats.greenhouse|acme|1"
    assert ats_key(None, "acme", "1") is None


def test_title_key():
    assert title_key("Senior Engineer", 2) == "senior engineer|lvl2"


def test_location_key():
    assert location_key(["New York, NY"], "onsite") == "new york, ny|onsite"


def test_desc_exact_hash_stable():
    assert desc_exact_hash("Hello  World") == desc_exact_hash("hello world")


def test_simhash_hamming():
    a = simhash64("python developer with django experience")
    b = simhash64("python developer with flask experience")
    assert hamming(a, b) < 64


def test_minhash_jaccard():
    base = "the quick brown fox jumps over the lazy dog and runs around the park every morning"
    sa = minhash_signature(base, num_perm=64)
    sb = minhash_signature(base.replace("jumps", "leaps"), num_perm=64)
    assert jaccard_est(sa, sb) > 0.3


def test_identical_minhash():
    s = minhash_signature("hello world foo bar", num_perm=16)
    assert jaccard_est(s, s) == 1.0
