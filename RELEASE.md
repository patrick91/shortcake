---
release type: patch
---

`sc create` also works from a detached checkout. It infers a unique local branch
at `HEAD`, falls back to the default branch while retaining any detached commits,
or accepts `--parent` when the intended base is ambiguous. Detached insert modes
remain rejected because their position requires a current branch.
