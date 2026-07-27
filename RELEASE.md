---
release type: minor
---

Redesign the diff switcher in `sc ui`. Stacks now render like `sc ls`: a
commit node per branch on a straight vertical rail, with a filled node on the
checked-out branch, instead of stair-step indentation. The list is anchored by
a trunk row, and independent stacks are grouped with separators — the active
stack first, the rest by most recent commit, stale roots dated.

The switcher also opens with the keyboard highlight on the branch being
viewed, filters with multi-word queries across branch name, commit subject,
and PR number (Esc clears the query first, closes second), dims shared date
prefixes in branch names, shows per-branch commit counts, marks merged and
draft PRs in the PR pill, and shows working-tree stats on the Working Changes
row.
