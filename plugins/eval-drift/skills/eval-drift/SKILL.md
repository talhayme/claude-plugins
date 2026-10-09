---
name: eval-drift
description: Per-case regression detection for `claude plugin eval`. The built-in eval scores a run but does not remember the previous one, so a case that passed yesterday and fails today is invisible unless the suite average happens to dip — and a change that fixes two cases while breaking one lifts the average and ships the break. This records each run's per-case outcome as a baseline and compares the next run against it case by case, blocking on any pass-to-fail flip regardless of the average. Also flags flaky cases and still-passing cases that got less reliable. Use after running `claude plugin eval`, before merging a plugin change, when setting up CI for a plugin, or when asked whether an eval result is safe to ship.
argument-hint: "[result.json] [--update-baseline]"
allowed-tools: Bash(python3:*), Bash(claude plugin eval:*), Read
---

# Eval drift

`claude plugin eval` tells you how a plugin scored. It does not tell you what
changed since last time. This skill does.

## When the user has a result already

If a `claude plugin eval --json <path>` result exists, compare it:

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/drift.py" <result.json> --baseline evals/baseline.json
```

Exit `1` means a regression — a case that passed in the baseline fails now.
Exit `0` means safe. Exit `2` means the result itself was unusable (partial
run, wrong schema) and no verdict was reached.

## When the user wants a fresh run

Run the eval first, writing JSON, then compare:

```bash
claude plugin eval . --json evals/results/latest.json --no-publish
python3 "${CLAUDE_SKILL_DIR}/../../scripts/drift.py" evals/results/latest.json
```

Each eval run makes real model calls against the user's account. Say so
before starting one, and prefer `--runs 1` on a first pass if cost is a
concern.

## Recording a baseline

Only from a run the user has confirmed is known-good:

```bash
python3 "${CLAUDE_SKILL_DIR}/../../scripts/drift.py" <result.json> --update-baseline
```

Do not update the baseline to make a regression go away. The baseline is the
contract; a failing comparison means the plugin changed, not the contract.
If the user intends the change — a case was redefined, a behaviour was
deliberately dropped — then updating is right, and say which cases moved.

## Reading the report

**Regressions** block. Name the case and the grader that failed; that is
where the user should look first.

**Less reliable** does not block: the case still passes, but its score fell
more than the tolerance. It is the early warning before a flip.

**Flaky** is a separate problem from regression. A case that passes on some
runs and fails on others cannot guard anything — its pass is noise. Say so
plainly; do not treat a flaky pass as evidence the plugin is fine.

**Improvements** are cases that failed in the baseline and pass now. Mention
them, and suggest `--update-baseline` so they are protected going forward.

**New and removed cases** are informational. A new case has no history and
cannot regress yet; a removed case may have been renamed.

## In CI

```yaml
- run: claude plugin eval . --trust-plugin --json result.json --no-publish
- run: python3 scripts/drift.py result.json --baseline evals/baseline.json
```

Commit `evals/baseline.json`. The second step fails the job on any
regression, independent of the suite average.
