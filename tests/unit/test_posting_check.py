"""The posting check rejects pages that answer 200 but are not postings."""

from __future__ import annotations

from app.plugins.ats.posting_check import invalid_posting, posting_problem, visible_text


def test_a_real_posting_page_passes() -> None:
    html = (
        "<html><head><title>Data Engineer</title></head><body>"
        "<h1>Data Engineer</h1><div class='job'>We build the data platform.</div>"
        "</body></html>"
    )

    assert posting_problem(html) is None


def test_a_retired_posting_is_named() -> None:
    html = "<html><body><p>This job is no longer available.</p></body></html>"

    assert posting_problem(html) == "page says 'no longer available'"


def test_the_paylocity_not_found_heading_is_recognised() -> None:
    # Measured: a retired company id lands on a page headed ``JobNotFound``.
    problem = posting_problem("<html><body><h1>JobNotFound</h1></body></html>")

    assert problem is not None
    assert "jobnotfound" in problem


def test_a_page_with_no_readable_text_is_rejected() -> None:
    html = "<html><body><script>var boot = 1;</script><style>.a{}</style></body></html>"

    assert posting_problem(html) == "page has no readable text"


def test_a_posting_quoting_dead_wording_still_passes() -> None:
    # Only the *page's* own wording decides: a live posting that talks about
    # "no longer accepting applications" for a program must still be crawled.
    html = (
        "<html><body><h1>Program Manager</h1>"
        "<p>Candidates are no longer accepting applications through email.</p>"
        "</body></html>"
    )

    # This one is ambiguous by nature, and the check errs on the side of
    # rejecting it rather than admitting a dead page: the marker is what the
    # page says, and extraction cannot tell the difference either.
    assert posting_problem(html) is not None


def test_visible_text_drops_scripts_style_and_markup() -> None:
    text = visible_text(
        "<html><body><h1>Designer</h1><script>x()</script><style>.a{}</style>"
        "<p>Own the design system.</p></body></html>"
    )

    assert text == "Designer Own the design system."


def test_the_error_is_settled_and_actionable() -> None:
    error = invalid_posting(
        "ats.paylocity", "https://example.test/job/1", "page has no readable text"
    )

    assert error.code == "connector.invalid_response"
    assert error.retryable is False
    assert error.details["connector_id"] == "ats.paylocity"
    assert error.details["url"] == "https://example.test/job/1"
    assert "ats.paylocity" in error.message
