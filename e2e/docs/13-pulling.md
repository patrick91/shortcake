# Pulling Changes from Remote

The `sc pull` command updates the current branch from its remote tracking branch.

This is useful when:
- You've pushed changes from one machine and want to get them on another
- A coworker has pushed to your branch
- You want to get the latest changes before continuing work

## Basic Pull - Already Up to Date

When your local branch matches the remote:

```console
$ sc pull
Already up to date.
```

## Pull with Fast-Forward

When the remote has new commits and your local branch can be fast-forwarded:

```console
$ sc pull
Fast-forwarded 'feature' to abc1234
```

## Pull with Diverged Branches

When branches have diverged (common after amending with `sc modify`), pull
automatically rebases your local commits onto the remote:

```console
$ sc pull
Rebased 'feature' onto origin/feature (def5678)
```

This is equivalent to:
1. `git fetch origin`
2. `git rebase origin/feature`

## Disable Auto-Rebase

Use `--no-rebase` to fail instead of rebasing when diverged:

```console
$ sc pull --no-rebase
Error: Branch 'feature' has diverged from 'origin/feature'. Use --rebase to rebase onto the remote branch.
```

## Common Scenarios

### Working on Multiple Machines

```
Machine A: sc create -m "Start feature" → make changes → git push
Machine B: sc co feature → sc modify → git push --force
Machine A: sc pull  ← rebases onto Machine B's changes
```

### Collaboration

When someone else pushes to your branch:

```
You: sc create -m "Feature" → git push
Coworker: sc co feature → adds commits → git push
You: sc pull  ← gets coworker's commits
```

## Options

| Option | Description |
|--------|-------------|
| `--no-rebase` | Fail instead of rebasing when branches have diverged |

## Error Cases

- **No remote configured**: `Error: No remote 'origin' configured.`
- **No tracking branch**: `Error: No remote tracking branch 'origin/feature'. Push your branch first.`
- **Uncommitted changes**: `Error: You have uncommitted changes. Commit or stash them first.`
- **Detached HEAD**: `Error: Not on a branch (detached HEAD).`
