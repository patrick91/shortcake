---
release type: patch
---

This relase fix sc adopt and sc sync issues.

- Fix `sc adopt` rewriting a branch's entire history (thousands of commits)
  when the new parent's head is not an ancestor of the branch — the walk now
  stops at the merge base, and a tracked branch is always detected as tracked
  regardless of where its trailer sits in the range.
- `sc sync` no longer deletes a merged branch when its children cannot be
  reparented (which orphaned the stack); it keeps the branch and explains how
  to resolve.
- `sc restack` on a branch whose parent was deleted now says so and suggests
  `sc adopt -f -p <new-parent>` instead of reporting "Everything up to date."
