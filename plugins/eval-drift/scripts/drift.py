#!/usr/bin/env python3
"""Per-case regression detection for `claude plugin eval`.

`claude plugin eval` scores a run. It does not remember the last one, so a
case that passed yesterday and fails today is invisible unless the suite
average happens to dip below the threshold — and a change that fixes two
cases while breaking one lifts the average and ships the break.

This stores each run's per-case outcome as a baseline and compares the next
run against it, case by case. A regression is a case that passed in the
baseline and fails now. That is what blocks the merge, independent of what
the average did.

Reads the `--json` output of `claude plugin eval`. No network, no model calls.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

# A case that failed on every run is clearly broken. One that passed on some
# runs and failed on others is flaky — a different problem, called out
# separately so it is not mistaken for a regression.
FLAKY_THRESHOLD = 0.0


@dataclass
class CaseOutcome:
    name: str
    score: float            # mean across runs, 0..1
    passed: bool            # score >= the suite threshold
    runs: int
    delta: float | None     # with-plugin minus without, when ablation ran
    failed_graders: list[str] = field(default_factory=list)

    @property
    def flaky(self) -> bool:
        """Some runs passed, some failed."""
        return 0.0 < self.score < 1.0


@dataclass
class Baseline:
    recorded_at: str
    claude_version: str
    threshold: float
    cases: dict[str, CaseOutcome]

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "recorded_at": self.recorded_at,
            "claude_version": self.claude_version,
            "threshold": self.threshold,
            "cases": {
                name: {
                    "score": round(c.score, 4),
                    "passed": c.passed,
                    "runs": c.runs,
                    "delta": None if c.delta is None else round(c.delta, 4),
                    "failed_graders": c.failed_graders,
                }
                for name, c in sorted(self.cases.items())
            },
        }

    @classmethod
    def from_json(cls, raw: dict[str, Any]) -> "Baseline":
        version = raw.get("schema_version")
        if version != SCHEMA_VERSION:
            raise ValueError(
                f"baseline schema {version!r} is not supported (expected {SCHEMA_VERSION}); "
                "re-record it with --update-baseline"
            )
        cases = {
            name: CaseOutcome(
                name=name,
                score=float(c["score"]),
                passed=bool(c["passed"]),
                runs=int(c.get("runs", 0)),
                delta=c.get("delta"),
                failed_graders=list(c.get("failed_graders", [])),
            )
            for name, c in raw.get("cases", {}).items()
        }
        return cls(
            recorded_at=raw.get("recorded_at", "?"),
            claude_version=raw.get("claude_version", "?"),
            threshold=float(raw.get("threshold", 1.0)),
            cases=cases,
        )


def _failed_graders(case: dict[str, Any]) -> list[str]:
    """Names of graders that failed on any 'with' run."""
    names: list[str] = []
    for run in (case.get("arms") or {}).get("with") or []:
        for grader in run.get("graders") or []:
            if grader.get("pass") is False and grader.get("name") not in names:
                names.append(grader["name"])
    return sorted(names)


def parse_result(raw: dict[str, Any]) -> Baseline:
    """Turn an `aggregate-result.json` into per-case outcomes."""
    if raw.get("schemaVersion") != 1:
        raise ValueError(
            f"unsupported eval result schema {raw.get('schemaVersion')!r}; expected 1"
        )
    if raw.get("partial"):
        # A partial run (cost ceiling hit, credential rejected) is not a
        # verdict about the plugin. Refuse rather than record half a baseline.
        raise ValueError(
            f"eval run is partial ({raw.get('partialReason') or 'unknown reason'}); "
            "not a usable result"
        )

    threshold = float((raw.get("suite") or {}).get("threshold", 1.0))
    cases: dict[str, CaseOutcome] = {}

    for case in raw.get("cases") or []:
        name = case.get("name")
        if not name:
            continue
        agg = case.get("aggregates") or {}
        score = float(agg.get("score", 0.0))
        with_runs = (case.get("arms") or {}).get("with") or []
        cases[name] = CaseOutcome(
            name=name,
            score=score,
            passed=score >= threshold,
            runs=len(with_runs),
            delta=agg.get("delta"),
            failed_graders=_failed_graders(case),
        )

    if not cases:
        raise ValueError("eval result contains no cases")

    return Baseline(
        recorded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        claude_version=str(raw.get("claudeVersion", "?")),
        threshold=threshold,
        cases=cases,
    )


@dataclass
class Drift:
    regressions: list[tuple[CaseOutcome, CaseOutcome]] = field(default_factory=list)
    improvements: list[tuple[CaseOutcome, CaseOutcome]] = field(default_factory=list)
    flaky: list[CaseOutcome] = field(default_factory=list)
    new_cases: list[CaseOutcome] = field(default_factory=list)
    removed_cases: list[str] = field(default_factory=list)
    score_drops: list[tuple[CaseOutcome, CaseOutcome]] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return bool(self.regressions)


def compare(current: Baseline, baseline: Baseline, *, drop_tolerance: float) -> Drift:
    """Case-by-case comparison. Pass/fail flips are what matter most."""
    drift = Drift()

    for name, now in current.cases.items():
        before = baseline.cases.get(name)
        if before is None:
            drift.new_cases.append(now)
            continue

        if before.passed and not now.passed:
            drift.regressions.append((before, now))
        elif not before.passed and now.passed:
            drift.improvements.append((before, now))
        elif now.passed and before.score - now.score > drop_tolerance:
            # Still passing, but noticeably less reliably than before. Not
            # blocking on its own; worth seeing before it becomes a flip.
            drift.score_drops.append((before, now))

        if now.flaky:
            drift.flaky.append(now)

    for name in baseline.cases:
        if name not in current.cases:
            drift.removed_cases.append(name)

    return drift


def render(current: Baseline, baseline: Baseline | None, drift: Drift | None) -> str:
    lines: list[str] = []
    passed = sum(1 for c in current.cases.values() if c.passed)
    total = len(current.cases)

    lines.append("")
    lines.append(f"  {passed}/{total} cases passed   threshold={current.threshold}   claude={current.claude_version}")

    if baseline is None or drift is None:
        lines.append("  No baseline found — nothing to compare against.")
        lines.append("  Record one with --update-baseline once this run is known-good.")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"  Baseline recorded {baseline.recorded_at} on claude {baseline.claude_version}")
    lines.append("")

    if drift.regressions:
        lines.append("  ✗ REGRESSIONS — passed in baseline, fail now")
        lines.append(f"  {'-' * 60}")
        for before, now in drift.regressions:
            graders = f"  ({', '.join(now.failed_graders)})" if now.failed_graders else ""
            lines.append(f"    {now.name:32} {before.score:.2f} → {now.score:.2f}{graders}")
        lines.append("")

    if drift.score_drops:
        lines.append("  ⚠ LESS RELIABLE — still passing, score dropped")
        lines.append(f"  {'-' * 60}")
        for before, now in drift.score_drops:
            lines.append(f"    {now.name:32} {before.score:.2f} → {now.score:.2f}")
        lines.append("")

    if drift.flaky:
        lines.append("  ⚠ FLAKY — passed on some runs, failed on others")
        lines.append(f"  {'-' * 60}")
        for case in drift.flaky:
            lines.append(f"    {case.name:32} score {case.score:.2f} over {case.runs} runs")
        lines.append("  A flaky case cannot guard anything. Fix the case or the plugin")
        lines.append("  before treating its pass as a signal.")
        lines.append("")

    if drift.improvements:
        lines.append("  ✓ IMPROVEMENTS — failed in baseline, pass now")
        lines.append(f"  {'-' * 60}")
        for before, now in drift.improvements:
            lines.append(f"    {now.name:32} {before.score:.2f} → {now.score:.2f}")
        lines.append("")

    if drift.new_cases:
        names = ", ".join(c.name for c in drift.new_cases)
        lines.append(f"  + new cases (no baseline yet): {names}")
    if drift.removed_cases:
        lines.append(f"  − cases removed since baseline: {', '.join(drift.removed_cases)}")
    if drift.new_cases or drift.removed_cases:
        lines.append("")

    if drift.blocking:
        lines.append(f"  DRIFT: {len(drift.regressions)} regression(s). Not safe to ship.")
    elif drift.improvements:
        lines.append("  No regressions. Improvements found — consider --update-baseline.")
    else:
        lines.append("  No regressions against baseline.")
    lines.append("")

    return "\n".join(lines)


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(f"error: {path} not found")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: {path} is not valid JSON: {exc}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compare a `claude plugin eval --json` result against a stored baseline, case by case."
    )
    parser.add_argument("result", type=Path, help="JSON written by `claude plugin eval --json <path>`")
    parser.add_argument("--baseline", type=Path, default=Path("evals/baseline.json"),
                        help="where the baseline lives (default: evals/baseline.json)")
    parser.add_argument("--update-baseline", action="store_true",
                        help="record this result as the new baseline instead of comparing")
    parser.add_argument("--drop-tolerance", type=float, default=0.15,
                        help="score drop on a still-passing case that counts as 'less reliable'")
    parser.add_argument("--json", action="store_true", help="emit a machine-readable verdict")
    args = parser.parse_args(argv)

    try:
        current = parse_result(load_json(args.result))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.update_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(current.to_json(), indent=2) + "\n", encoding="utf-8")
        print(f"baseline recorded: {args.baseline} ({len(current.cases)} cases)")
        return 0

    baseline: Baseline | None = None
    if args.baseline.exists():
        try:
            baseline = Baseline.from_json(load_json(args.baseline))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    drift = compare(current, baseline, drop_tolerance=args.drop_tolerance) if baseline else None

    if args.json:
        verdict = {
            "blocking": bool(drift and drift.blocking),
            "has_baseline": baseline is not None,
            "regressions": [n.name for _, n in (drift.regressions if drift else [])],
            "improvements": [n.name for _, n in (drift.improvements if drift else [])],
            "flaky": [c.name for c in (drift.flaky if drift else [])],
            "score_drops": [n.name for _, n in (drift.score_drops if drift else [])],
            "new_cases": [c.name for c in (drift.new_cases if drift else [])],
            "removed_cases": list(drift.removed_cases) if drift else [],
        }
        print(json.dumps(verdict, indent=2))
    else:
        print(render(current, baseline, drift))

    return 1 if drift and drift.blocking else 0


if __name__ == "__main__":
    sys.exit(main())
