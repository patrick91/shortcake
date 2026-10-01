---
release type: patch
---

Fix restacks dropping commits from branches with more than one commit. `sc modify -m`
copies the `Shortcake-Parent` trailer onto each new commit, and restacking (via
`sc sync`, `sc restack`, or `sc pull`) treated only the newest of them as the
branch, silently dropping the earlier commits.
