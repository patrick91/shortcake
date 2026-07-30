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
Sync · main

  pulling main from origin…
  checking 1 branch…

✓ main already up to date · 1 branch checked, nothing to clean up
```

### Sync with Merged Branches

When a branch is merged into trunk, sync detects it and offers to delete:

```console
$ # Simulate merge by fast-forwarding main to feature
$ git checkout main && git merge add-feature --ff-only > /dev/null
$ echo "post merge" > post.txt && git add post.txt && git commit -m "Post merge commit" > /dev/null
$ sc sync --yes
Sync · main

  pulling main from origin…
  checking 1 branch…
Cleaning up 1 branch

  ● add-feature  deleted

✓ main already up to date · 1 branch deleted
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
Sync · main

  pulling main from origin…
  checking 1 branch…
Would delete merged branch 'new-feature'
✓ main already up to date · 1 branch checked, nothing to clean up
$ sc sync --yes
Sync · main

  pulling main from origin…
  checking 1 branch…
Cleaning up 1 branch

  ● new-feature  deleted

✓ main already up to date · 1 branch deleted
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
Sync · main

  pulling main from origin…
  checking 1 branch…
Cleaning up 1 branch

  ● squash-feature  deleted

✓ main already up to date · 1 branch deleted
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
Sync · main

  pulling main from origin…
  checking 1 branch…
✓ main already up to date · 1 branch checked, nothing to clean up
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
Sync · main

  pulling main from origin…
  checking 2 branches…
Cleaning up 1 branch

  ● feature-a   deleted
  ● feature-b   reparented onto main


✓ main already up to date · 1 branch deleted · 1 reparented
```

After reparenting, the stack shows feature-b directly above main:

```console
$ sc ls
◉ feature-b (current)
│ Feature B
│
◯ main
  After merge
```

### Sync Never Deletes Trunk

After ff-merging a tracked branch, trunk may appear "tracked" due to
the merged commit's trailer. Sync must never offer to delete the trunk:

```console
$ # reset-to-main
$ echo "trunk safe" > safe.py && git add safe.py
$ sc create -m "Trunk safe feature"
Created branch 'trunk-safe-feature' from 'main'
$ git checkout main > /dev/null 2>&1 && git merge trunk-safe-feature --ff-only > /dev/null
$ echo "post" > post.txt && git add post.txt && git commit -m "Post merge" > /dev/null
$ sc sync --yes
Sync · main

  pulling main from origin…
  checking 1 branch…
Cleaning up 1 branch

  ● trunk-safe-feature  deleted

✓ main already up to date · 1 branch deleted
```

Main still exists and is not deleted:

```console
$ git branch --list main
* main
```

### Sync with GitHub-Detected Merged PRs

When a branch was squash-merged on GitHub (which local git can't detect), sync uses the GitHub API to find merged PRs:

```console
$ # reset-to-main
$ # github: setup-mock-with-remote
$ echo "gh feature" > gh.py && git add gh.py
$ sc create -m "GH feature"
Created branch 'gh-feature' from 'main'
$ git push -u origin gh-feature > /dev/null 2>&1
$ # github: add-pr gh-feature 50 main
$ # github: merge-pr 50
$ sc sync --yes
Sync · test/repo · main

  pulling main from origin…
  checking 1 branch…
Cleaning up 1 branch

  ● gh-feature  deleted

✓ main already up to date · 1 branch deleted
```

### Sync with Closed (Not Merged) PRs

When a PR is closed without merging, sync can detect and offer to delete the branch:

```console
$ # reset-to-main
$ # github: reset-state
$ echo "closed feature" > closed.py && git add closed.py
$ sc create -m "Closed feature"
Created branch 'closed-feature' from 'main'
$ git push -u origin closed-feature > /dev/null 2>&1
$ # github: add-pr closed-feature 60 main
```

Note: The closed PR detection requires the PR to be in "closed" state (not "open"). The mock server currently only supports merging PRs (which sets state to "closed" with merged_at set). A PR closed without merge would need the mock to support closing without merging.

### Sync with Uncommitted Changes

Sync refuses to run when there are uncommitted changes:

```console
$ echo "dirty" >> closed.py
$ sc sync
Error: You have uncommitted changes. Commit or stash them first.
$ git checkout -- closed.py
```