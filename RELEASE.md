---
release type: minor
---

`sc ui` now shows your stack in a sidebar on the left of the diff, instead of
only in the header's branch menu. The stack you're viewing is drawn as an
`sc ls`-style rail with the trunk, PR numbers and CI status, and every branch
shows its commit, commit count and `+/−` line stats against its parent. Date
prefixes are dropped from branch names, and other stacks fold away under
"Other stacks". The "Jump to branch…" (⌘K) search sits at the top of the
sidebar.

The changed-files tree moves to the right of the diff. Below 1200px it folds
into the header's file menu, and below 768px the stack sidebar hides and the
branch search moves into the header. The header now shows the commit title
and `branch → parent` of the diff you're viewing.

The stack API (`/api/stack`, `/api/state`) now includes `diffStats` for each
branch, cached per commit pair so polling stays cheap.
