"""S3: FakeBrowserProvider returns static FormModel (no Playwright; no UI wiring)."""

from pathlib import Path

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


def test_fake_browser_provider_has_no_playwright_dependency():
    # Checking ``sys.modules`` only proves whether Playwright happens to be
    # installed in this environment; the contract is that the fake module does
    # not depend on it at all, so scan the module itself.
    import tests.support.fakes as fakes

    source = Path(fakes.__file__).read_text(encoding="utf-8").lower()
    assert "playwright" not in source

    provider = FakeBrowserProvider()
    assert provider.id == "browser.fake"
