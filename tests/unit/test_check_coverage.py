"""Coverage gates (spec section 19.1): grouping and threshold boundaries."""

from __future__ import annotations

from scripts.check_coverage import Gate, GateCoverage, combine, gate_for, report

DOMAIN = Gate("domain", ("app/domain",), 90.0)
APPLICATION = Gate("application", ("app/application",), 85.0)
ADAPTERS = Gate("adapters", ("app/infrastructure", "app/plugins"), 75.0)
GATES = (DOMAIN, APPLICATION, ADAPTERS)


def test_threshold_is_inclusive() -> None:
    assert GateCoverage(DOMAIN, statements=100, missing=10).passed
    assert not GateCoverage(DOMAIN, statements=100, missing=11).passed


def test_layer_without_statements_fails_instead_of_passing_trivially() -> None:
    empty = GateCoverage(DOMAIN, statements=0, missing=0)

    assert empty.percent == 0.0
    assert not empty.passed


def test_paths_are_matched_on_package_boundaries() -> None:
    assert gate_for("app/domain/services/matching.py", GATES) is DOMAIN
    assert gate_for("app/application/config.py", GATES) is APPLICATION
    assert gate_for("app/plugins/ats/lever/__init__.py", GATES) is ADAPTERS
    assert gate_for("app/infrastructure/http/httpx_client.py", GATES) is ADAPTERS
    # A sibling package with a shared prefix is not part of the layer.
    assert gate_for("app/domains/extra.py", GATES) is None
    # The worker has no section 19.1 floor.
    assert gate_for("app/worker/main.py", GATES) is None


def test_adapters_combine_infrastructure_and_plugins() -> None:
    results = {
        result.gate: result
        for result in combine(
            {
                "app/domain/models/job.py": (40, 4),
                "app/application/config.py": (60, 6),
                "app/plugins/ats/lever/__init__.py": (30, 9),
                "app/infrastructure/http/httpx_client.py": (70, 21),
                "app/worker/main.py": (50, 50),
            },
            GATES,
        )
    }

    assert results[DOMAIN].statements == 40
    assert results[DOMAIN].missing == 4
    assert results[APPLICATION].missing == 6
    assert results[ADAPTERS].statements == 100
    assert results[ADAPTERS].missing == 30
    assert results[ADAPTERS].percent == 70.0
    assert not results[ADAPTERS].passed


def test_report_names_each_layer_with_its_floor() -> None:
    text = report(combine({"app/domain/x.py": (10, 1)}, GATES))

    assert "domain" in text
    assert "90.0%" in text
    assert "pass" in text
    assert "FAIL" in text  # the packages the sample data never covered
