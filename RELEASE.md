---
release type: patch
---

Prune deleted remote branches when fetching. A plain fetch leaves
`refs/remotes/origin/<branch>` behind after the branch is deleted upstream, so
the stale ref lingers indefinitely and anything reading it believes the branch
still exists on the remote. That affects merged-branch detection in `sc sync`
and remote lookups in `sc checkout`.

This was invisible for anyone with `fetch.prune = true` in their git config;
shortcake no longer depends on that setting.
