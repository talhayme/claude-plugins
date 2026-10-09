---
name: context-audit
description: Measure what installed skills and plugins cost in context on every request. Reports total tokens added per turn, a per-plugin breakdown, descriptions truncated past the 1,536-character limit, skills missing a description, and skills whose descriptions overlap so much that Claude must guess between them. Use when context feels tight, when a session hits its limit sooner than expected, after installing several plugins, when deciding what to uninstall, or when the user asks where their context window is going.
argument-hint: "[--budget N] [--json]"
allowed-tools: Bash(python3:*), Read
---

# Context audit

Every installed skill injects its name, description and `when_to_use` into
the system prompt of **every request** — whether the skill is used or not.
Ten plugins at eight skills each is a standing cost per turn that nothing in
the interface itemises.

This skill measures that cost and finds what is wasting it.

## Run it

```!
python3 "${CLAUDE_SKILL_DIR}/../../scripts/audit.py" $ARGUMENTS
```

## Reading the result

The report has three parts.

**Total and per-source breakdown.** The headline number is tokens added to
every request. The breakdown attributes it to each plugin, so a single heavy
plugin is immediately visible rather than hidden in an aggregate.

**Heaviest skills.** Ranked by cost. A skill over ~120 tokens of description
is doing more than helping Claude choose — that detail belongs in the skill
body, which is loaded only when the skill actually fires.

**Problems.** Three kinds, each actionable:

- *Truncated* — the description exceeds 1,536 characters, so Claude Code cuts
  it. The author's trailing instructions never reach the model, and the skill
  is being selected on incomplete information. Shorten it.
- *No description* — Claude selects skills by description. Without one it can
  only match on the name, so the skill rarely fires when it should.
- *Overlapping* — two or more skills describe similar work. When they compete
  for the same request Claude picks one semi-arbitrarily and the others are
  pure overhead. Usually means a duplicate install or a plugin that should be
  narrowed.

## After the report

Report the total first, then the largest single source, then the problems
worth acting on. Do not list all skills — the top few and the flagged ones
are what matters.

When recommending removals, be concrete about the saving: "uninstalling X
returns ~N tokens per request." Do not recommend removing something the user
has clearly been using.

Two caveats to state honestly if the numbers are quoted back:

- Token counts are estimated at ~4 characters per token. Good for ranking and
  budgeting, not exact.
- This measures the *description* cost paid every turn. A skill's body is
  loaded only when it fires, and is not counted here.

## Flags

- `--budget N` — exit non-zero when the total exceeds N tokens. For CI, to
  stop a repository's `.claude/skills` from growing unnoticed.
- `--json` — machine-readable output.
- `--top N` — how many heaviest skills to list (default 12).
