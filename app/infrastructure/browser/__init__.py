"""Browser infrastructure (spec §13).

Playwright never leaks past this package: callers see the domain ports only.
"""

from __future__ import annotations

from app.infrastructure.browser.playwright_provider import (
    PlaywrightBrowserProvider,
    playwright_available,
)

__all__ = ["PlaywrightBrowserProvider", "playwright_available"]
