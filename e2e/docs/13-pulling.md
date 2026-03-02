# Pulling Changes from Remote

The `sc pull` command updates the current branch from its remote tracking branch.

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
Reset 'add-feature' to origin/add-feature (<HASH>)
```

This is equivalent to:
1. `git fetch origin`
2. `git reset --hard origin/feature`

This is the expected behavior for stacked PR workflows where the remote is the
source of truth after force-pushing amended commits.

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
| `--rebase`, `-r` | Rebase local commits onto remote instead of resetting |

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
