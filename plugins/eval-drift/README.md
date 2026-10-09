# eval-drift

**Per-case regression detection for `claude plugin eval`.**

The built-in eval scores a run. It does not remember the previous one. So a
case that passed yesterday and fails today is invisible unless the suite
average happens to dip below the threshold — and a change that fixes two
cases while breaking one *raises* the average and ships the break.

This records each run's per-case outcome as a baseline and compares the next
run against it, case by case. A regression is a case that passed before and
fails now. That blocks the merge, whatever the average did.

```
  3/4 cases passed   threshold=1.0   claude=2.1.293
  Baseline recorded 2026-10-09T12:40:11+00:00 on claude 2.1.293

  ✗ REGRESSIONS — passed in baseline, fail now
  ------------------------------------------------------------
    finds-duplicates                 1.00 → 0.00  (skill-fired)

  + new cases (no baseline yet): brand-new-case

  DRIFT: 1 regression(s). Not safe to ship.
```

## Install

```bash
claude plugin marketplace add talhayme/claude-plugins
claude plugin install eval-drift@talhayme
```

## Use

Run the eval with JSON output, then compare:

```bash
claude plugin eval . --json evals/results/latest.json --no-publish
python3 scripts/drift.py evals/results/latest.json
```

Or in plain language — the skill fires on its own:

> Is this eval result safe to ship? What changed since the baseline?

### Record a baseline

From a run you know is good:

```bash
python3 scripts/drift.py evals/results/latest.json --update-baseline
```

Commit `evals/baseline.json`. Don't update it to make a regression
disappear: the baseline is the contract. If a case was deliberately
redefined, updating is right — and the report will tell you exactly which
cases moved.

## What it reports

| Signal | Blocks? | Meaning |
|---|---|---|
| **Regression** | yes | Passed in baseline, fails now |
| **Less reliable** | no | Still passing, score fell past tolerance — the warning before a flip |
| **Flaky** | no | Passed some runs, failed others — its pass is noise, not evidence |
| **Improvement** | no | Failed in baseline, passes now — consider updating the baseline |
| **New / removed** | no | No history yet / possibly renamed |

Flaky is called out separately on purpose. A flaky case cannot guard
anything, and treating its pass as a signal is how regressions get through.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | No regressions (or no baseline yet) |
| `1` | At least one regression — do not ship |
| `2` | Result unusable: partial run, wrong schema, no cases. No verdict reached |

A **partial** run (cost ceiling hit, credential rejected) is refused rather
than recorded. Half a baseline is worse than none: it would turn an aborted
run into a verdict about the plugin.

## In CI

```yaml
- run: claude plugin eval . --trust-plugin --json result.json --no-publish
- run: python3 scripts/drift.py result.json --baseline evals/baseline.json
```

The second step fails the job on any per-case regression, independent of
the suite average.

## Relationship to `claude plugin eval`

This does not replace it and does not re-run anything. It reads the
`--json` result the built-in tool already produces and adds the one thing
that tool lacks: memory of the last run. The schema is pinned
(`schemaVersion: 1`) and a mismatch is a clean error, not a wrong answer.

## Requirements

Python 3.9+. No dependencies. No network, no model calls — the comparison
is pure arithmetic over two JSON files.

## Tests

```bash
python3 -m pytest tests/ -q
# 26 passed
```

Fixtures mirror the documented `aggregate-result.json` shape and cover:
clean rerun, pass→fail, fail→pass, flaky, score drop within and beyond
tolerance, new and removed cases, partial-run refusal, schema mismatch, and
every CLI exit code.

## License

MIT
