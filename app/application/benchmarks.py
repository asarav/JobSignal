"""Benchmark harness for the hot read paths (M11).

The scenarios are the reads a large local database touches most often. Each one
is measured several times and the median is compared with a checked-in budget
(and, when present, the recorded baseline times). The harness never asserts a
speedup on a noisy machine; it reports measurements and a conservative budget
verdict so a regression is visible in CI.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from app.application.commands.jobs import JobsUseCase
from app.application.dto import JobFilter, JobSearchParams, RankedJobParams
from app.application.keywords import KeywordInsightsService

#: The hot read paths the approved M11 plan names for this harness. This tuple
#: is the contract: ``tests/integration/test_benchmarks.py`` pins the exact names
#: independently, and every entry must be measured and present in the baseline.
NAMED_HOT_PATHS: tuple[str, ...] = (
    "jobs.list",
    "jobs.ranked",
    "applications.get_analytics",
    "keywords.prevalence",
    "keywords.trends",
)

#: Additional reads measured because the ranking surfaces depend on them and the
#: spec's performance budget (§17.1) names the job search/detail reads.
EXTRA_SCENARIOS: tuple[str, ...] = (
    "jobs.search",
    "jobs.get_detail",
    "companies.get_detail",
)

#: Concrete scenario list: every named path plus the extra measured reads.
SCENARIOS: tuple[str, ...] = NAMED_HOT_PATHS + EXTRA_SCENARIOS

#: Budgets in milliseconds for the single-process desktop target. They are
#: deliberately loose so slower machines do not fail on noise; the baseline file
#: carries the measured times and a 1.25x tolerance on top.
#:
#: Absolute slack added to the baseline comparison: the larger of 1.25x the
#: baseline and the baseline plus this many milliseconds. It exists because the
#: scenarios run in single-digit milliseconds, where timer/scheduler noise is a
#: few milliseconds and a bare 1.25x rule flaps between identical runs. It is
#: deliberately small (5 ms) so a real regression — for example a 6 ms read
#: becoming 25 ms — still fails; only a handful of milliseconds of noise are
#: excused. Every scenario is warmed up once before timing, which keeps the
#: median stable enough for this floor.
BASELINE_SLACK_MS = 5.0

BUDGETS_MS: dict[str, float] = {
    "jobs.list": 250.0,
    "jobs.ranked": 600.0,
    "applications.get_analytics": 400.0,
    "keywords.prevalence": 250.0,
    "keywords.trends": 400.0,
    "jobs.search": 400.0,
    "jobs.get_detail": 200.0,
    "companies.get_detail": 250.0,
}


@dataclass(slots=True)
class Measurement:
    scenario: str
    median_ms: float
    samples_ms: list[float] = field(default_factory=list)
    budget_ms: float = 0.0
    baseline_ms: float | None = None
    within_budget: bool = True
    within_baseline: bool = True


def baseline_limit_ms(baseline_ms: float) -> float:
    """The largest median accepted without calling it a regression.

    The 1.25x rule is the real threshold; the small absolute floor only absorbs
    timer noise on single-digit-millisecond reads. See ``BASELINE_SLACK_MS``.
    """

    return max(baseline_ms * 1.25, baseline_ms + BASELINE_SLACK_MS)


async def _measure(
    name: str,
    work: Callable[[], Awaitable[Any]],
    *,
    repeats: int,
) -> Measurement:
    samples: list[float] = []
    # One untimed warm-up: the first read of a scenario pays lazy imports and
    # first-query setup that are not part of steady-state cost.
    await work()
    for _ in range(max(1, repeats)):
        started = time.perf_counter()
        await work()
        samples.append((time.perf_counter() - started) * 1000.0)
    value = round(median(samples), 2)
    budget = BUDGETS_MS.get(name, 1000.0)
    return Measurement(
        scenario=name,
        median_ms=value,
        samples_ms=[round(sample, 2) for sample in samples],
        budget_ms=budget,
        within_budget=value <= budget,
    )


async def run_scenarios(
    container: Any,
    *,
    repeats: int = 3,
    baseline: Mapping[str, Any] | None = None,
) -> list[Measurement]:
    jobs = JobsUseCase(container)
    keywords = KeywordInsightsService(container)
    from app.application.analytics import OutcomeAnalyticsService
    from app.application.commands.companies import CompaniesUseCase

    analytics = OutcomeAnalyticsService(container)
    companies = CompaniesUseCase(container)
    async with container.uow_factory() as uow:
        company_rows = await uow.companies.list()
        job_rows = await uow.jobs.list(limit=1)
        company_id = str(company_rows[0]["id"]) if company_rows else ""
        job_id = str(job_rows[0]["id"]) if job_rows else ""
        await uow.commit()
    work: dict[str, Callable[[], Awaitable[Any]]] = {
        "jobs.list": lambda: jobs.list(JobFilter(limit=50)),
        "jobs.ranked": lambda: jobs.ranked(RankedJobParams(limit=50)),
        "applications.get_analytics": lambda: analytics.get_analytics(),
        "keywords.prevalence": lambda: keywords.prevalence(limit=50),
        "keywords.trends": lambda: keywords.trends(periods=12),
        "jobs.search": lambda: jobs.search(JobSearchParams(query="engineer")),
        "jobs.get_detail": lambda: jobs.get_detail(job_id),
        "companies.get_detail": lambda: companies.get_detail(company_id),
    }
    results: list[Measurement] = []
    recorded = dict((baseline or {}).get("scenarios") or {})
    for name in SCENARIOS:
        measured = await _measure(name, work[name], repeats=repeats)
        entry = recorded.get(name)
        if isinstance(entry, dict) and isinstance(entry.get("median_ms"), (int, float)):
            measured.baseline_ms = float(entry["median_ms"])
            limit = baseline_limit_ms(measured.baseline_ms)
            measured.within_baseline = measured.median_ms <= limit
        results.append(measured)
    return results


def summarize(results: Sequence[Measurement]) -> dict[str, Any]:
    return {
        "scenarios": {
            item.scenario: {
                "median_ms": item.median_ms,
                "samples_ms": item.samples_ms,
                "budget_ms": item.budget_ms,
                "baseline_ms": item.baseline_ms,
                "within_budget": item.within_budget,
                "within_baseline": item.within_baseline,
            }
            for item in results
        },
        "pass": all(item.within_budget and item.within_baseline for item in results),
    }


__all__ = [
    "BASELINE_SLACK_MS",
    "BUDGETS_MS",
    "EXTRA_SCENARIOS",
    "NAMED_HOT_PATHS",
    "SCENARIOS",
    "Measurement",
    "baseline_limit_ms",
    "run_scenarios",
    "summarize",
]
