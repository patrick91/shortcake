---
release type: patch
---

Keep pulled PR branches visible when an unrelated local branch points at the
same commit. Shortcake now uses the uniquely matching remote branch as the
canonical stack branch while leaving the local alias untracked.
