"""Enforce the per-layer coverage gates of spec section 19.1.

CI measures the whole suite once with ``pytest --cov=app``; this script reads
that data and checks each layer against its own floor. A single global number
would let a large, lightly-tested adapter layer hide a regression in the domain,
which is the layer the gate exists to protect.

Usage:
    python -m pytest -q --cov=app
    python scripts/check_coverage.py

Exits 1 when a layer is below its floor, 2 when there is no data to check.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_DATA_FILE = ROOT / ".coverage"


@dataclass(frozen=True)
class Gate:
    """One layer and the line-coverage floor it must clear."""

    name: str
    #: Package directories, relative to the repository root.
    paths: tuple[str, ...]
    threshold: float


@dataclass(frozen=True)
class GateCoverage:
    gate: Gate
    statements: int
    missing: int

    @property
    def percent(self) -> float:
        if self.statements == 0:
            return 0.0
        return 100.0 * (self.statements - self.missing) / self.statements

    @property
    def passed(self) -> bool:
        # An empty layer means the measurement never saw those files (a wrong
        # --cov target, for instance); that must fail rather than pass trivially.
        if self.statements == 0:
            return False
        return self.percent >= self.gate.threshold


#: Spec section 19.1: domain >= 90%, application >= 85%, adapters >= 75%.
GATES: tuple[Gate, ...] = (
    Gate("domain", ("app/domain",), 90.0),
    Gate("application", ("app/application",), 85.0),
    Gate("adapters", ("app/infrastructure", "app/plugins"), 75.0),
)


def relative_path(filename: str, root: Path = ROOT) -> str | None:
    """Return ``filename`` as a project-relative POSIX path, or None if outside."""
    try:
        return Path(filename).resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def gate_for(path: str, gates: Sequence[Gate] = GATES) -> Gate | None:
    """The first gate covering ``path``; None when no gate applies."""
    for gate in gates:
        for prefix in gate.paths:
            if path == prefix or path.startswith(prefix + "/"):
                return gate
    return None


def combine(
    measured: Mapping[str, tuple[int, int]],
    gates: Sequence[Gate] = GATES,
) -> list[GateCoverage]:
    """Sum ``{path: (statements, missing)}`` into one row per gate."""
    totals = {gate: [0, 0] for gate in gates}
    for path, (statements, missing) in measured.items():
        gate = gate_for(path, gates)
        if gate is None:
            continue
        totals[gate][0] += statements
        totals[gate][1] += missing
    return [
        GateCoverage(gate, statements, missing)
        for gate, (statements, missing) in totals.items()
    ]


def measure(data_file: Path, root: Path = ROOT) -> dict[str, tuple[int, int]]:
    """Read a coverage data file into ``{relative path: (statements, missing)}``."""
    import coverage

    coverage_file = coverage.Coverage(data_file=str(data_file))
    coverage_file.load()
    measured: dict[str, tuple[int, int]] = {}
    for filename in coverage_file.get_data().measured_files():
        relative = relative_path(filename, root)
        if relative is None or not Path(filename).exists():
            continue
        try:
            _, statements, _, missing, _ = coverage_file.analysis2(filename)
        except coverage.exceptions.NoSource:
            continue
        measured[relative] = (len(statements), len(missing))
    return measured


def report(results: Iterable[GateCoverage]) -> str:
    lines = ["Coverage gates (spec section 19.1):"]
    for result in results:
        status = "pass" if result.passed else "FAIL"
        lines.append(
            f"  {result.gate.name:<12} {result.percent:6.1f}%  "
            f">= {result.gate.threshold:5.1f}%  {status}   "
            f"({result.statements} statements, {result.missing} missing)"
        )
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the section 19.1 coverage gates.")
    parser.add_argument("--data-file", type=Path, default=DEFAULT_DATA_FILE)
    args = parser.parse_args(list(argv) if argv is not None else None)

    if not args.data_file.exists():
        print(
            f"No coverage data at {args.data_file}; run pytest with --cov=app first.",
            file=sys.stderr,
        )
        return 2

    results = combine(measure(args.data_file))
    print(report(results))
    failed = [result for result in results if not result.passed]
    if failed:
        names = ", ".join(result.gate.name for result in failed)
        print(f"\nBelow the section 19.1 floor: {names}.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
