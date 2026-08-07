---
release type: patch
---

Publish linear Shortcake PR sequences through GitHub's public-preview stacked
pull request API. GitHub now renders the stack natively and can merge or rebase
it as a unit; Shortcake keeps commit trailers as the local source of truth,
uses PR-body maps only as a fallback, and exposes native membership in
`sc ls --refresh` and JSON output.

`sc submit` creates, extends, or safely recreates native stacks while refusing
partial destructive changes. Existing PR-body stacks migrate atomically: a
scoped submit retains their complete compatibility map until
`sc submit --stack` selects every open layer. `sc checkout <PR>` automatically
brings a GitHub-created stack into local Shortcake trailers, with divergence
protection and `sc continue`/`sc abort` conflict recovery.
