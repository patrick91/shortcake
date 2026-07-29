---
release type: minor
---

Redesign `sc submit` output around a single live stack tree. The plan tree is
now the progress display: rows keep their place while their markers and status
column fill in, instead of the plan being printed and then replaced by three
flat lines per branch. The header states the target repo and whether PRs are
drafts, PR numbers are hyperlinked, and the footer links the top of the stack —
or lists each tip when the stack forks. Failed branches keep their position so
you can see where a stack broke.

`sc submit` now asks what to submit with a menu whose tree previews the
highlighted choice, and it offers a real Cancel — answering "no" to the old
prompt still submitted the downstack. `--stack` on a forked stack asks before
sweeping in a sibling arm, offering to submit just your own arm instead.

`sc restack` renders through the same view. Output adapts to the terminal: the
tree drops spacing before it hides any branch, and neither the progress tree
nor the menu can be cropped. Piped output streams row by row, and `--json` is
unchanged.
