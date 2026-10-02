---
release type: patch
---

Fix `sc move`, `sc restack` and other restacking commands crashing when a branch
they need to rebase is checked out in another worktree. Shortcake now rebases it
on a detached HEAD and moves the branch from inside its worktree, so that
worktree's files follow. It refuses with a clear error when that worktree has
uncommitted changes or an operation in progress, and `sc continue` / `sc abort`
keep the worktree in sync after a conflict.

Also fix `sc move` crashing with a `KeyError` when the branch to move doesn't
exist; it now reports that the branch wasn't found.
