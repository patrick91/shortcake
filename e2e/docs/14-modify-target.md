# Folding Changes into Another Branch

The `sc modify --target` command lets you fold staged changes into another branch's commit without switching branches manually.

## Setup: Create a Stack

Create a stack of three branches: a → b → c.

```console
$ echo "base code" > app.py && git add app.py
$ sc create -m "Add base app"
Created branch 'add-base-app' from 'main'
```

```console
$ echo "feature b" > feature_b.py && git add feature_b.py
$ sc create -m "Add feature B"
Created branch 'add-feature-b' from 'add-base-app'
```

```console
$ echo "feature c" > feature_c.py && git add feature_c.py
$ sc create -m "Add feature C"
Created branch 'add-feature-c' from 'add-feature-b'
```

Verify the stack:

```console
$ sc ls
◉ add-feature-c (current)
│
◯ add-feature-b
│
◯ add-base-app
│
◯ main
```

## Fold Staged Changes into Another Branch

From branch c, modify a file that belongs to branch b, stage it, and fold into b:

```console
$ echo "feature b updated" > feature_b.py && git add feature_b.py
$ sc modify -t add-feature-b
Folded staged changes into 'add-feature-b'
Restacked 1 branch(es).
```

We're still on branch c:

```console
$ git branch --show-current
add-feature-c
```

Verify the change landed in branch b's diff:

```console
$ git diff add-base-app..add-feature-b -- feature_b.py
diff --git a/feature_b.py b/feature_b.py
new file mode 100644
index 0000000..08cec9b
--- /dev/null
+++ b/feature_b.py
@@ -0,0 +1 @@
+feature b updated
```

## Failure Recovery: Patch Incompatible with Target

When a patch can't be applied to the target branch, the operation rolls back cleanly.

First, create a stash to verify it's preserved after the failed rollback:

```console
$ echo "stashed work" >> app.py
$ git stash push -m "important work"
Saved working directory and index state On add-feature-c: important work
```

Stage a change to a file that only exists on branch c, then try to fold into branch a where it doesn't exist:

```console
$ echo "updated c" > feature_c.py && git add feature_c.py
$ sc modify -t add-base-app
Error: Unexpected error: Failed to apply patch: error: feature_c.py: does not exist in index
```

Verify rollback restored the original state — still on branch c with staged changes preserved and the stash intact:

```console
$ git branch --show-current
add-feature-c
$ git diff --cached --name-only
feature_c.py
$ git stash list
stash@{0}: On add-feature-c: important work
```

## Failure Recovery: Unstaged Changes Preserved

When the fold fails and the user has unstaged working tree changes, those changes must survive the rollback.

Clear the restored staging from the previous test, then make unstaged edits, stage a modification to feature_b.py (which doesn't exist on add-base-app), and try to fold:

```console
$ git reset HEAD -- feature_c.py > /dev/null && git checkout -- feature_c.py
$ echo "unstaged work" >> app.py
$ echo "wip on c" >> feature_c.py
$ echo "modify b" >> feature_b.py && git add feature_b.py
$ sc modify -t add-base-app
Error: Unexpected error: Failed to apply patch: error: feature_b.py: does not exist in index
```

Verify staged changes are restored, unstaged changes are preserved, and the stash from the previous test is intact:

```console
$ git branch --show-current
add-feature-c
$ git diff --cached --name-only
feature_b.py
$ git diff --name-only
app.py
feature_c.py
$ git stash list
stash@{0}: On add-feature-c: important work
```

## Incompatible Options

The `--target` flag cannot be combined with `-m` or `-e`:

```console
$ sc modify -t add-feature-b -m "message"
Error: Cannot use both -t and -m
```

```console
$ sc modify -t add-feature-b -e
Error: Cannot use both -t and -e
```
