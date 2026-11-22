"""Manual probe: fill a *live* third-party application form, never submit.

The fixture suite proves the pipeline against pages we control. This script
runs the same code path against a real ATS surface (Lever's public demo board)
so the field classifier, inspector and filler are checked against markup we did
not write. It is a manual probe, not part of CI: it needs the network, a
Chromium build and the optional browser extra.

    python scripts/live_ats_fill.py [apply-url] [--headed]

Nothing here can submit. The provider arms its submission interceptor for the
whole session, so any POST that looks like a submission is aborted; the script
asserts that and leaves the form filled for a human to inspect.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.models.browser import AnswerBook, OpenOptions  # noqa: E402
from app.domain.models.common import Err  # noqa: E402
from app.domain.services.form_filler import GenericFormFiller  # noqa: E402
from app.domain.services.form_inspector import inspect_html  # noqa: E402
from app.infrastructure.browser.playwright_provider import (  # noqa: E402
    PlaywrightBrowserProvider,
)

DEFAULT_URL = (
    "https://jobs.lever.co/leverdemo/ad208490-4052-4f91-a57e-433f2d1e484b/apply"
)

ANSWERS = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "full_name": "Ada Lovelace",
    "email": "ada.lovelace@example.com",
    "phone": "+1 555 0100",
    "current_employer": "Analytical Engines Ltd",
    "current_title": "Principal Engineer",
    "linkedin_url": "https://www.linkedin.com/in/ada-lovelace",
    "github_url": "https://github.com/ada",
    "website": "https://ada.example.com",
    "city": "London",
    "region": "England",
    "postal_code": "SW1A 1AA",
    "country": "United Kingdom",
    # A card-style yes/no question; only the box whose own label matches the
    # stored answer may be ticked.
    "work_authorization": "yes",
    "location": "London, United Kingdom",
}


def _line(char: str = "-", width: int = 78) -> None:
    print(char * width)


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", nargs="?", default=DEFAULT_URL)
    parser.add_argument(
        "--headed",
        action="store_true",
        help="show the browser window instead of running it headless",
    )
    parser.add_argument(
        "--screenshot",
        default=str(ROOT / "lodestar-test-data" / "live-ats-fill.png"),
        help="where to save the final screenshot",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="wait for Enter before closing so a human can look at the form",
    )
    args = parser.parse_args()

    provider = PlaywrightBrowserProvider()
    exit_code = 1
    try:
        outcome = await provider.open(
            args.url, OpenOptions(headed=args.headed, timeout_s=60)
        )
        if isinstance(outcome, Err):
            print(f"open failed: {outcome.error.code}: {outcome.error.message}")
            return 1
        session_id = outcome.value.session_id
        print(f"opened {outcome.value.url}")
        print(f"session {session_id}  interceptor_active={provider.interceptor_active(session_id)}")

        html = await provider.snapshot_dom(session_id)
        model = inspect_html(html, session_id=session_id, url=outcome.value.url)
        print(f"inspected {len(model.fields)} fields, {len(model.controls)} controls")

        filler = GenericFormFiller(delay_s=0.05)
        plan = filler.plan(model, AnswerBook(answers=ANSWERS))

        _line("=")
        print(f"{'FIELD':38} {'KIND':10} {'ACTION':6} REASON")
        _line()
        for item in plan.items:
            label = (item.field_label or item.field_id)[:37]
            print(f"{label:38} {item.kind:10} {item.action:6} {item.reason}")
        _line("=")

        report = await filler.apply(provider.handle(session_id), plan)
        print(f"filled  {len(report.filled)}")
        print(f"skipped {len(report.skipped)}")
        print(f"blocked {len(report.blocked)}")
        print(f"errors  {len(report.errors)}")
        for error in report.errors:
            print(f"  error: {error}")

        page = provider.page(session_id)
        _line()
        print("read-back from the live DOM:")
        for item in report.filled:
            try:
                value = await page.locator(item.selector).first.input_value()
            except Exception as exc:  # noqa: BLE001 - probe output only
                value = f"<unreadable: {exc}>"
            print(f"  {item.field_label:38} = {value!r}")

        # A page handler may post seconds after the write; give it the chance.
        await page.wait_for_timeout(3000)
        print()
        print(f"submitted            = {report.submitted}")
        print(f"blocked_requests     = {provider.blocked_requests}")
        print(f"interceptor_active   = {provider.interceptor_active(session_id)}")

        screenshot = Path(args.screenshot)
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        await provider.handle(session_id).screenshot(str(screenshot))
        print(f"screenshot           = {screenshot}")

        if args.keep_open:
            await asyncio.to_thread(input, "press Enter to close the browser... ")

        passed = (
            report.submitted is False
            and provider.interceptor_active(session_id) is True
            and bool(report.filled)
        )
        print("RESULT:", "PASS" if passed else "FAIL")
        exit_code = 0 if passed else 1
    finally:
        await provider.close_all()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
