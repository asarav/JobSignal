"""S3: FakeBrowserProvider returns static FormModel (no Playwright; no UI wiring)."""

import pytest
from app.domain.models.browser import BrowserPage, OpenOptions
from tests.support.fakes import FakeBrowserProvider


@pytest.mark.asyncio
async def test_fake_browser_provider_static_form():
    provider = FakeBrowserProvider()
    result = await provider.open("https://example.com/apply", OpenOptions())
    from app.domain.models.common import Ok
    assert isinstance(result, Ok)
    page = result.value
    assert isinstance(page, BrowserPage)
    assert page.session_id == "s1"
    assert await provider.close_all() is None


def test_fake_browser_provider_no_playwright():
    import sys
    # Ensure no Playwright import leaks into the fake provider module.
    __import__("tests.support.fakes", fromlist=["FakeBrowserProvider"])
    assert "playwright" not in sys.modules