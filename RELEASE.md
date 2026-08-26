---
release type: patch
---

Prevent `sc move` from crashing after a successful local move when the branch's
former parent has already been deleted locally. Post-move stack discovery now
skips missing local branch names instead of indexing a nonexistent ref.
