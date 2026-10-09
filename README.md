# claude-plugins

Plugins for Claude Code, built around one idea: **AI tooling should be
measurable.** If a thing cannot be measured, it cannot be kept honest.

## Install

```bash
claude plugin marketplace add talhayme/claude-plugins
```

Then install what you need:

```bash
claude plugin install context-audit@talhayme
```

## Plugins

### [context-audit](plugins/context-audit)

Measures what your installed skills cost in context on **every request** —
a standing per-turn cost that nothing in the interface itemises. Reports the
total, attributes it per plugin, and flags three specific wastes: descriptions
truncated past the 1,536-character limit, skills with no description at all,
and skills whose descriptions overlap so much that Claude has to guess
between them.

Runs offline, read-only, no dependencies. Ships an eval suite.

```
/context-audit
/context-audit --budget 4000
```

### [eval-drift](plugins/eval-drift)

Per-case regression detection for `claude plugin eval`. The built-in eval
scores a run but has no memory of the last one, so a change that fixes two
cases and breaks one lifts the average and ships the break. This records a
baseline and blocks any case that passed before and fails now — independent
of the average. Also separates flaky cases from real regressions, because a
flaky pass is noise, not evidence.

Reads the `--json` result the built-in tool already produces. No model
calls, no network. 26 tests.

```
claude plugin eval . --json result.json --no-publish
python3 scripts/drift.py result.json          # exit 1 on regression
```

## Building on these

Every plugin here ships `evals/` and passes `claude plugin validate --strict`.
If you send a pull request, please keep both.

## Author

Vitalii Bogachev — AI engineer working on LLM products in production.
[Portfolio](https://talhayme.github.io/portfolio-vitalii) ·
[Notes](https://talhayme.github.io/blog) ·
[GitHub](https://github.com/talhayme)

## License

MIT
