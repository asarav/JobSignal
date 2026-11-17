"""``rendered_dom``: fields from a JavaScript-rendered page (M2).

This strategy opens the page in a headless browser, waits for the DOM to
settle, and then reuses the static and generic heuristics on the rendered
HTML. It is the last resort before ``partial`` and only runs when a browser
provider is configured.

Robots and challenge rules (spec §17 / M2 exit): a page that disallows
crawling, or that is a challenge/CAPTCHA page, is not extracted at all.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.domain.models.common import Err, Ok, PortError, Result
from app.domain.models.extraction import ExtractionResult, ExtractionTarget
from app.plugins.extraction.base import StrategyBase
from app.plugins.extraction.generic import GenericStrategy
from app.plugins.extraction.static_html import StaticHtmlStrategy

if TYPE_CHECKING:
    from app.domain.models.context import ExecutionContext

CHALLENGE_MARKERS = (
    "just a moment",
    "cf-challenge",
    "challenge-platform",
    "captcha",
    "are you human",
    "verify you are human",
    "access denied",
)


class RenderedDomStrategy(StrategyBase):
    id = "extract.rendered_dom"
    default_order = 35

    def __init__(self, browser: Any = None) -> None:
        self._browser = browser

    def can_attempt(self, target: ExtractionTarget) -> bool:
        return self._browser is not None and bool(target.url)

    async def extract(
        self, target: ExtractionTarget, ctx: ExecutionContext
    ) -> Result[ExtractionResult, PortError]:
        if self._browser is None:
            return Err(
                PortError(
                    code="rendered_dom.unavailable",
                    message="no browser provider is configured",
                    retryable=False,
                )
            )
        # Robots: a disallowed URL is not fetched, even for rendering.
        if not getattr(self._browser, "robots_allowed", lambda _u: True)(target.url):
            return Err(
                PortError(
                    code="robots.disallowed",
                    message=f"robots.txt disallows {target.url}",
                    retryable=False,
                )
            )
        opened = await self._browser.open(target.url, _open_options())
        if not isinstance(opened, Ok):
            return opened
        session_id = opened.value.session_id
        try:
            html = await self._browser.rendered_html(session_id)
        finally:
            await self._browser.close_all()
        if not html:
            return Err(
                PortError(
                    code="rendered_dom.empty",
                    message=f"rendered page was empty: {target.url}",
                    retryable=True,
                )
            )
        lowered = html.lower()
        if any(marker in lowered for marker in CHALLENGE_MARKERS):
            return Err(
                PortError(
                    code="rendered_dom.challenge_detected",
                    message=f"challenge page detected for {target.url}",
                    retryable=False,
                )
            )
        # Reuse the static and generic heuristics on the rendered DOM.
        rendered_target = ExtractionTarget(
            url=target.url,
            source_id=target.source_id,
            html=html,
            content_type=target.content_type,
        )
        merged_fields: dict[str, Any] = {}
        merged_fields.update(StaticHtmlStrategy().fields(rendered_target))
        merged_fields.update(
            {k: v for k, v in GenericStrategy().fields(rendered_target).items() if k not in merged_fields}
        )
        if not merged_fields:
            return Err(
                PortError(
                    code="extraction.no_fields",
                    message=f"rendered_dom found no fields in {target.url}",
                    retryable=False,
                )
            )
        # Delegate to the base class to build the result, but keep our own
        # provenance and confidence.
        result = StaticHtmlStrategy().to_result(rendered_target, merged_fields)
        # Replace the strategy id with our own for provenance.
        from dataclasses import replace

        return Ok(
            replace(
                result,
                strategy_id=self.id,
                field_confidence={
                    name: min(confidence, 0.6) for name, confidence in result.field_confidence.items()
                },
            )
        )


def _open_options() -> Any:
    from app.domain.models.browser import OpenOptions

    return OpenOptions(timeout_s=30.0)
