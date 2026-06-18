---
release type: patch
---

**`sc ui`**: the diff pane now lists files in the same order as the sidebar
file tree (folders first, then files, natural sort) instead of raw git-diff
order, so the two stay in sync as you scroll. The ordering reuses the file
tree's own sort, so it's guaranteed to match.
