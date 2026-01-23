# Syncing with Remote

## The `sc sync` Command

The `sc sync` command syncs your local repository with the remote:
1. Fetches and fast-forwards trunk (main/master)
2. Detects branches that have been merged into trunk
3. Prompts to delete merged branches
4. Reparents children of deleted branches
5. Restacks remaining branches

### Setup: Create a Stack

```console
$ echo "feature code" > feature.py && git add feature.py
$ sc create -m "Add feature"
Created branch 'add-feature' from 'main'
```

### Basic Sync - Nothing to Do

When everything is up to date, sync reports nothing to do:

```console
$ sc sync
Pulling main from remote...
Checking for merged branches...
Everything up to date.
```

### Sync with Merged Branches

When a branch is merged into trunk, sync detects it and offers to delete:

```console
$ # Simulate merge by fast-forwarding main to feature
$ git checkout main && git merge add-feature --ff-only > /dev/null
$ echo "post merge" > post.txt && git add post.txt && git commit -m "Post merge commit" > /dev/null
$ sc sync --force
Pulling main from remote...
Checking for merged branches...
Deleted branch add-feature
```

### Dry Run Mode

Use `--dry-run` to preview what would happen without making changes:

```console
$ echo "new feature" > new.py && git add new.py
$ sc create -m "New feature"
Created branch 'new-feature' from 'main'
$ git checkout main && git merge new-feature --ff-only > /dev/null
$ echo "another post merge" > post2.txt && git add post2.txt && git commit -m "Another post merge" > /dev/null
$ sc sync --dry-run
Pulling main from remote...
Checking for merged branches...
Would delete merged branch 'new-feature'
Everything up to date.
$ sc sync --force
Pulling main from remote...
Checking for merged branches...
Deleted branch new-feature
```

## The `--sync` Flag on Restack

The `--sync` flag on `sc restack` fetches from remote before restacking. It can detect diverged branches.

### Setup: Create a Stack with Remote

```console
$ # setup: with-remote
$ echo "feature code" > feature2.py && git add feature2.py
$ sc create -m "Add feature 2"
Created branch 'add-feature-2' from 'main'
$ git push -u origin add-feature-2
branch 'add-feature-2' set up to track 'origin/add-feature-2'.
```

### Basic Sync

When everything is up to date, sync reports nothing to do:

```console
$ sc restack --sync
Fetching from origin...
Everything up to date.
```

### Auto-Rebase on Divergence

When a branch has diverged from remote (both have unique commits), restack auto-rebases:

```console
$ echo "local change" >> feature2.py && git add feature2.py && git commit -m "Local change" > /dev/null
$ # remote: force-push add-feature-2
$ git fetch origin
$ sc restack --sync
Fetching from origin...
Rebasing 'add-feature-2' onto 'origin/add-feature-2'...
Everything up to date.
```

### Fixing Divergence

Rebase onto the remote branch to incorporate the remote changes:

```console
$ git rebase origin/add-feature-2
Current branch add-feature-2 is up to date.
$ sc restack --sync
Fetching from origin...
Everything up to date.
```
