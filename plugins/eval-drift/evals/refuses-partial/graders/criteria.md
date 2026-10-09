---
type: llm
---

PASS if the response declines to record the baseline because the run was
partial (cost ceiling / incomplete), explains that a partial run is not a
verdict about the plugin, and does not claim the baseline was updated.

FAIL if it reports the baseline as recorded, or ignores that the run was
partial.
