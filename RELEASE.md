---
release type: patch
---

Fix `sc sync` crashing when a branch that needs reparenting is checked out in
another worktree. Sync now rebases it in that worktree and keeps the parent branch
when uncommitted changes or an operation in progress prevent reparenting.
