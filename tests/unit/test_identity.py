"""Unit tests for identity resolution."""
from __future__ import annotations

from app.domain.models.identity import JobFingerprintSet
from app.domain.services.identity import resolve


def _fp(**kw):
    defaults = dict(
        url_key="https://example.com/jobs/1",
        ats_key="ats.greenhouse|acme|1",
        company_id="co",
        title_key="backend engineer",
        title_family="backend_engineer",
        location_key="new york, ny|onsite",
        desc_exact_hash="h1",
        desc_simhash64=0,
        desc_minhash=[],
        department="eng",
        salary_min=100000,
        req_ids=(),
    )
    defaults.update(kw)
    return JobFingerprintSet(**defaults)


def test_exact_alias_identical():
    inc = _fp(url_key="https://example.com/jobs/2", ats_key="ats.greenhouse|acme|1")
    cand = _fp()
    d = resolve(inc, [cand])
    assert d.repost_class == "exact_duplicate"
    assert d.action == "attach_alias"


def test_new_position_no_candidates():
    inc = _fp(url_key="https://example.com/jobs/9", ats_key="ats.greenhouse|acme|9", company_id="other")
    d = resolve(inc, [])
    assert d.repost_class == "new_position"


def test_similar_distinct_different_location():
    inc = _fp(location_key="london, uk|onsite", url_key="https://example.com/jobs/2", ats_key="ats.greenhouse|acme|2")
    cand = _fp()
    d = resolve(inc, [cand])
    # different location should not merge as reposted_identical
    assert d.repost_class in ("similar_distinct", "new_position")


def test_materially_updated():
    inc = _fp(desc_exact_hash="h2", url_key="https://example.com/jobs/1b", ats_key="ats.greenhouse|acme|1")
    cand = _fp()
    d = resolve(inc, [cand])
    # exact alias hit, content differs
    assert d.repost_class in ("materially_updated", "reposted_identical")
    assert d.action in ("new_snapshot", "attach_alias")


def test_ambiguous_tie_is_similar_distinct():
    inc = _fp(
        url_key="https://example.com/jobs/new",
        ats_key=None,
        desc_exact_hash="h-new",
        desc_minhash=[1, 2, 3],
        desc_simhash64=0,
    )
    a = _fp(
        url_key="https://example.com/jobs/a",
        ats_key=None,
        desc_exact_hash="ha",
        desc_minhash=[1, 2, 3],
        desc_simhash64=0,
    )
    b = _fp(
        url_key="https://example.com/jobs/b",
        ats_key=None,
        desc_exact_hash="hb",
        desc_minhash=[1, 2, 3],
        desc_simhash64=0,
    )
    d = resolve(inc, [a, b])
    assert d.ambiguous is True
    assert d.repost_class == "similar_distinct"
    assert d.action == "new_job"


def test_changed_requirement_skills_are_material():
    inc = _fp(desc_exact_hash="h1", req_skills=("python", "kubernetes"))
    cand = _fp(desc_exact_hash="h1", req_skills=("python", "terraform"))
    d = resolve(inc, [cand])
    assert d.material is True
    assert d.repost_class == "materially_updated"
    assert d.action == "new_snapshot"


def test_guard_req_ids_prevents_merge():
    inc = _fp(req_ids=("REQ-2",), url_key="https://example.com/jobs/2", ats_key="ats.greenhouse|acme|2")
    cand = _fp(req_ids=("REQ-1",))
    d = resolve(inc, [cand])
    assert d.repost_class in ("similar_distinct", "new_position")
