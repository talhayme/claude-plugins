# context-audit

**Find out what your installed skills cost you on every single request.**

Every skill injects its name, description and `when_to_use` into the system
prompt of every request — used or not. Ten plugins at eight skills each is a
standing tax per turn that nothing in the interface itemises.

On the machine this was built on, the answer was **6,630 tokens across 61
skills**, half of it from one source, with a duplicate install nobody had
noticed.

```
  61 skills across 18 sources
  ~6,630 tokens added to every request

  BY SOURCE
  --------------------------------------------------------------
  personal                      3,439 tok   51.9%  █████████████
  plugin-dev@claude-plugins-of    766 tok   11.6%  ███
  pentest@claude-pentest          426 tok    6.4%  ██
  mcp-server-dev@claude-plugin    347 tok    5.2%  █

  HEAVIEST SKILLS (top 4)
  --------------------------------------------------------------
    252 tok  docs                           personal          [verbose]
    241 tok  pptx                           personal          [verbose]
    240 tok  google-workspace               personal          [verbose]
    239 tok  xlsx                           personal          [verbose]

  ⚠ OVERLAPPING SKILLS
  --------------------------------------------------------------
    [accuracy, analysis, benchmark] skill-creator (personal),
                                    skill-creator (skill-creator@official)
```

## Install

```bash
claude plugin marketplace add talhayme/claude-plugins
claude plugin install context-audit@talhayme
```

Or run it once without installing:

```bash
claude --plugin-dir ./context-audit
```

## Use

Ask in plain language — the skill fires on its own:

> How much context are my skills costing me?

> Do I have any redundant plugins I could remove?

Or invoke it directly:

```
/context-audit
/context-audit --budget 4000
/context-audit --json
```

## What it finds

**Cost, attributed.** Total tokens added per request, broken down by plugin.
A single heavy plugin shows up immediately instead of hiding in an aggregate.

**Truncated descriptions.** Claude Code cuts name + description +
`when_to_use` at 1,536 characters. Past that, the author's instructions never
reach the model — so the skill is being selected on incomplete information
and nothing warns you.

**Missing descriptions.** Claude picks skills by description. Without one it
can only match on the name, so the skill rarely fires when it should.

**Overlapping skills.** Two skills describing similar work compete for the
same request. Claude picks one semi-arbitrarily; the other is pure overhead.
Usually a duplicate install or a plugin that wants narrowing.

## In CI

Stop a repository's `.claude/skills` from growing unnoticed:

```bash
python3 scripts/audit.py --budget 4000
# exit 1 when the total exceeds the budget
```

```yaml
- name: Context budget
  run: python3 scripts/audit.py --budget 4000 --project-dir .
```

## How it measures

Reads the on-disk layout — `~/.claude/skills`, every plugin under
`~/.claude/plugins/marketplaces/*/plugins/*/skills`, and `.claude/skills` in
the current project. No network, no API calls, read-only.

Token counts are estimated at ~4 characters per token. That is good enough to
rank skills and set a budget, and not exact — the real tokeniser will differ
by a few percent.

It measures the **description** cost, paid on every turn. A skill's body is
loaded only when the skill fires and is not counted here. That is the point:
the body is a cost you choose, the description is one you always pay.

## Requirements

Python 3.9+. No dependencies.

## Tests

The plugin ships an eval suite:

```bash
claude plugin eval .
```

Two cases: the skill fires and reports a concrete total, and it surfaces
overlapping skills rather than listing everything.

## License

MIT
