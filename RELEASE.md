---
release type: minor
---

`sc ui` now shows your stack in a sidebar on the left of the diff, instead of
only in the header's branch menu. The stack you're viewing is drawn as an
`sc ls`-style rail with the trunk, PR numbers and CI status. Date prefixes are
dropped from branch names, and only the branch you're viewing expands to show
its commit and diff stats. Other stacks fold away under "Other stacks".

The changed-files tree moves to the right of the diff. Below 1200px it folds
into the header's file menu, and below 768px the stack sidebar hides and the
header's "Jump to branch…" (⌘K) search takes over. The header now shows the
commit title and `branch → parent` of the diff you're viewing.
