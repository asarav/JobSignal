"""The test container must never launch a real browser.

A rendered-DOM crawl of a fixture URL used to navigate the network and stall
the suite. Tests get ``FakeBrowserProvider`` unless they inject a double
explicitly.
"""

from __future__ import annotations

from app.infrastructure.browser.playwright import PlaywrightBrowserProvider
from tests.support.containers import build_test_container
from tests.support.fakes import FakeBrowserProvider


def test_test_containers_default_to_the_fake_browser() -> None:
    container = build_test_container()
    browser = container.extra.get("browser")
    assert isinstance(browser, FakeBrowserProvider)
    assert not isinstance(browser, PlaywrightBrowserProvider)


def test_an_explicit_browser_override_is_kept() -> None:
    fake = FakeBrowserProvider(html="<html></html>")
    container = build_test_container(browser=fake)
    assert container.extra.get("browser") is fake
