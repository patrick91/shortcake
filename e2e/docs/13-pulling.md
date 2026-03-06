# Pulling Changes from Remote

The `sc pull` command updates the current branch and its entire stack from remote.

This is useful when:
- You've pushed changes from one machine and want to get them on another
- A coworker has pushed to your branch
- You want to get the latest changes before continuing work

## Setup: Create a Remote and a Branch

```console
$ # setup: with-remote
$ echo "feature code" > feature.py && git add feature.py
$ sc create -m "Add feature"
Created branch 'add-feature' from 'main'
$ git push -u origin add-feature > /dev/null 2>&1
```

## Basic Pull - Already Up to Date

When your local branch matches the remote:

```console
$ sc pull
Already up to date.
```

## Pull with Diverged Branches

When branches have diverged (common after amending with `sc modify`), pull
automatically resets to match the remote:

```console
$ # remote: force-push add-feature
$ sc pull
Updated 'add-feature' to origin/add-feature (<HASH>)
```

This is equivalent to:
1. `git fetch origin`
2. `git reset --hard origin/feature`

This is the expected behavior for stacked PR workflows where the remote is the
source of truth after force-pushing amended commits.

## Pull Entire Stack

When you have a stack of branches and some have been updated on remote,
`sc pull` fetches once and updates all branches in the stack, then restacks:

```console
$ # setup: with-remote
$ # reset-to-main
$ echo "feature a" > a.py && git add a.py
$ sc create -m "Feature A"
Created branch 'feature-a' from 'main'
$ git push -u origin feature-a > /dev/null 2>&1
$ echo "feature b" > b.py && git add b.py
$ sc create -m "Feature B"
Created branch 'feature-b' from 'feature-a'
$ git push -u origin feature-b > /dev/null 2>&1
$ sc pull
Already up to date.
```

After someone force-pushes to a branch in the stack:

```console
$ # remote: force-push feature-a
$ sc pull
Updated 'feature-a' to origin/feature-a (<HASH>)
Rebasing 'feature-b' onto 'feature-a'...
Restacked 1 branch(es).
```

## Error Cases

Cannot pull without a remote:

```console
$ git remote remove origin
$ sc pull
Error: No remote 'origin' configured.
```

## Options

| Option | Description |
|--------|-------------|
| `--rebase`, `-r` | Rebase local commits onto remote instead of resetting (single-branch mode) |

## Common Scenarios

### Working on Multiple Machines

```
Machine A: sc create -m "Start feature" → make changes → git push
Machine B: sc co feature → sc modify → git push --force
Machine A: sc pull  ← resets to Machine B's changes
```

### Collaboration

When someone else pushes to your branch:

```
You: sc create -m "Feature" → git push
Coworker: sc co feature → adds commits → git push
You: sc pull  ← gets coworker's commits
```
