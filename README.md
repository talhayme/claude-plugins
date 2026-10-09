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
