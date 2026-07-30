---
release type: minor
---

`sc sync` now asks once instead of once per branch. It used to walk three
separate loops — locally merged, merged on GitHub, closed PR — prompting `[y/n]`
for each, so you approved a deletion without seeing the others and only learned
afterwards that it had also reparented branches and removed worktrees.

Everything is stated up front instead, one action per section, and the choice is
about local copies: for a merged branch the commits are already in the trunk, so
the local branch is a redundant copy. That is not true of a closed PR whose
remote branch is also gone — the local branch is then the only copy. Those are
marked, the question says so, and an extra option deletes only what is
recoverable.

Piped and CI runs are unchanged: nothing is deleted and the hint still points at
`--yes`.
