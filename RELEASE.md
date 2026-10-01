---
release type: patch
---

Fix `sc sync` keeping a merged branch when several stacked branches were merged at
once. Children of a deleted branch were moved onto its parent even when that parent
was being deleted too, which conflicted, so sync kept the branch and then tried to
rebase it onto trunk. Children now move onto the nearest ancestor that survives the
sync, and a merged branch that could not be deleted is no longer rebased.
