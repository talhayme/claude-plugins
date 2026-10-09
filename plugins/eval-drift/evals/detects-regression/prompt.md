---
name: detects-regression
description: Given a baseline and a newer result where one case flipped to failing, the skill names that case and says the change is not safe.
max_turns: 8
allowed_tools: [Skill, Bash, Read]
---

There is an eval baseline at tests/fixtures/good.json (treat it as the recorded
baseline) and a new eval result at tests/fixtures/regressed.json. Is the new
result safe to ship? Which cases changed?
