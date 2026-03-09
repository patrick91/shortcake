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

### Sync with Squash-Merged Branches

Sync detects squash merges — when a branch's changes are applied to trunk
as a single commit (not a fast-forward merge):

```console
$ echo "squash feature" > squash.py && git add squash.py
$ sc create -m "Squash feature"
Created branch 'squash-feature' from 'main'
$ # Simulate squash merge: apply same change on main as a new commit
$ git checkout main > /dev/null
$ echo "squash feature" > squash.py && git add squash.py && git commit -m "squash: add squash feature" > /dev/null
$ sc sync --yes
Pulling main from remote...
Checking for merged branches...
Deleted branch squash-feature
```

### Sync Does Not Delete Branches with Independent Changes

When trunk independently modifies the same files as a branch (without
actually merging the branch), sync correctly keeps the branch:

```console
$ echo "original" > shared.txt && git add shared.txt && git commit -m "Add shared file" > /dev/null
$ echo "branch change" > shared.txt && git add shared.txt
$ sc create -m "Modify shared file"
Created branch 'modify-shared-file' from 'main'
$ # Independently modify same file on main with different content
$ git checkout main > /dev/null
$ echo "independent main change" > shared.txt && git add shared.txt && git commit -m "chore: independent change" > /dev/null
$ sc sync --yes
Pulling main from remote...
Checking for merged branches...
Everything up to date.
```

### Sync with Stacked Branches and Reparenting

When a branch in the middle of a stack is merged, sync deletes it and reparents its children:

```console
$ # reset-to-main
$ echo "feature a" > a.py && git add a.py
$ sc create -m "Feature A"
Created branch 'feature-a' from 'main'
$ echo "feature b" > b.py && git add b.py
$ sc create -m "Feature B"
Created branch 'feature-b' from 'feature-a'
$ git checkout main && git merge feature-a --ff-only > /dev/null
$ echo "after merge" > post.txt && git add post.txt && git commit -m "After merge" > /dev/null
$ git checkout feature-b
$ sc sync --yes
Pulling main from remote...
Checking for merged branches...
Reparented feature-b to main
Deleted branch feature-a
```

After reparenting, the stack shows feature-b directly above main:

```console
$ sc ls
◉ feature-b (current)
│
◯ main
```

### Sync with Uncommitted Changes

Sync refuses to run when there are uncommitted changes:

```console
$ echo "dirty" >> b.py
$ sc sync
Error: You have uncommitted changes. Commit or stash them first.
$ git checkout -- b.py
```