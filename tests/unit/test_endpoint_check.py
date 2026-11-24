"""The endpoint classifier is pure; every state has a fixture here."""

from __future__ import annotations

import pytest
from app.domain.services.endpoint_check import classify_endpoint, visible_text

JOB_PAGE = (
    "<html><body><h1>Data Engineer</h1>"
    "<p>Own our ingestion pipelines.</p>"
    "<form action='/apply'><button>Apply</button></form></body></html>"
)


def test_a_live_posting_is_ok() -> None:
    state = classify_endpoint(
        status=200,
        final_url="https://acme.test/jobs/1",
        html=JOB_PAGE,
        requested_url="https://acme.test/jobs/1",
    )
    assert state == "ok"


@pytest.mark.parametrize("status", [404, 410])
def test_gone_statuses(status: int) -> None:
    assert classify_endpoint(status=status) == "gone_404_410"


def test_login_walls_and_blocks_are_their_own_state() -> None:
    assert classify_endpoint(status=401) == "auth_required"
    assert classify_endpoint(status=403) == "blocked"
    assert classify_endpoint(status=429) == "blocked"


def test_server_errors_and_timeouts() -> None:
    assert classify_endpoint(status=503) == "blocked"
    assert classify_endpoint(status=500) == "error"
    assert classify_endpoint(status=None) == "timeout"
    assert classify_endpoint(status=200, timed_out=True) == "timeout"


def test_closure_wording_is_detected() -> None:
    state = classify_endpoint(
        status=200,
        html="<html><body><p>This position has been filled.</p></body></html>",
        requested_url="https://acme.test/jobs/1",
    )
    assert state == "closed_message"


def test_negated_closure_wording_is_not_a_closure() -> None:
    state = classify_endpoint(
        status=200,
        html=(
            "<html><body><p>Applications are not closed; apply any time. "
            "<button>Apply</button></p></body></html>"
        ),
        requested_url="https://acme.test/jobs/1",
    )
    assert state == "ok"


def test_a_redirect_to_a_generic_careers_page() -> None:
    state = classify_endpoint(
        status=200,
        final_url="https://acme.test/careers",
        html=JOB_PAGE,
        requested_url="https://acme.test/jobs/1",
    )
    assert state == "generic_redirect"


def test_a_page_with_no_application_affordance_is_form_unavailable() -> None:
    state = classify_endpoint(
        status=200,
        html="<html><body><h1>Data Engineer</h1><p>Read about the role.</p></body></html>",
        requested_url="https://acme.test/jobs/1",
    )
    assert state == "form_unavailable"


def test_visible_text_drops_markup_and_code() -> None:
    text = visible_text(
        "<html><body><h1>Designer</h1><script>x()</script>"
        "<style>.a{}</style><p>Own the system.</p></body></html>"
    )
    assert text == "Designer Own the system."
