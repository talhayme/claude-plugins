---
type: llm
---

PASS if the response says the result is NOT safe to ship, names
"finds-duplicates" as the case that regressed (passed before, fails now), and
does not describe the overall average as sufficient reason to merge.

FAIL if it calls the result safe, fails to name the regressed case, or only
reports an aggregate score.
