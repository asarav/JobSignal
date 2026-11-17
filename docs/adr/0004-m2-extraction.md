# ADR 0004 — Milestone 2 universal extraction

- Status: accepted
- Date: 2026-11-16
- Scope: Milestone 2 (universal extraction)

## Context

Milestone 1 crawled postings and stored them, but every posting was whatever
the ATS connector happened to return. Milestone 2 must make extraction
**universal**: an unknown careers page must yield at least a partial record,
and every field must carry the strategy that produced it.

## Decisions

### 1. Strategies are plugins behind the `ExtractionStrategy` port

Six strategies (`public_endpoint`, `jsonld`, `embedded_json`, `static_html`,
`generic`, `partial`) plus `rendered_dom`, all implementing the same port and
registered as `extraction` plugins. The pipeline is built from the registry, so
disabling a strategy in `plugins.list` removes it from the pipeline without a
code change.

### 2. Field-level merge by confidence

`ExtractionPipeline.extract` runs the strategies in order and merges their
results with `merge_results`, which keeps, per field, the value with the
highest confidence. On a tie the earlier strategy wins, because the pipeline
is ordered by trust (structured data first, heuristics last). A failing
strategy is skipped, so one broken strategy cannot destroy a record.

### 3. Per-source extraction order

`SourceConfig.extraction_order` lets a source say which strategies to try
first. The pipeline moves those to the front and runs the rest by
`default_order`.

### 4. Rendered DOM is optional

Playwright is an optional dependency (`lodestar[render]`). `RenderedDomStrategy`
returns `False` from `can_attempt` when no browser is configured, so the
pipeline skips it and the rest of the strategies still run. The strategy also
refuses to extract a page that disallows crawling or that looks like a
challenge/CAPTCHA page.

### 5. HTML parsing uses the standard library

`app/infrastructure/html/parsing.py` is built on `html.parser`. No third-party
parser is added, keeping the dependency footprint small (spec §22.7).

### 6. ATS connectors run through the isolation shim

Connectors are registered as `connector` plugins and invoked through
`PluginRegistry.invoke`, which applies the timeout and circuit breaker. A
hanging board can no longer block the worker.

### 7. `PortError` is no longer frozen

Frozen dataclasses cannot have `__traceback__` assigned to them, which broke
on Python 3.14 when an exception was raised inside an async generator. `Ok`,
`Err` and the other value types stay frozen; only the exception does not.

## Consequences

- Every strategy is contract-tested against the same interface, and the
  pipeline is independently testable with stubs.
- An unknown ATS page still yields a (partial) record, because `partial` is
  always available and never fails.
- Playwright is opt-in: the core install and CI stay light, and the
  rendered-DOM strategy degrades to a no-op without it.
- The extraction pipeline is built from the registry, so a disabled strategy
  is automatically skipped everywhere.
