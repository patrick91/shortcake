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
$ sc sync --yes
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
$ sc sync --yes
Pulling main from remote...
Checking for merged branches...
Deleted branch new-feature
```
