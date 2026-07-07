# Modifying Commits

The `sc modify` command helps you amend commits or create new ones while preserving Shortcake tracking.

## Setup: Create a Tracked Branch

```console
$ echo "initial code" > app.py && git add app.py
$ sc create -m "Add application"
Created branch 'add-application' from 'main'
```

## Amending with Staged Changes

Make changes and amend the current commit:

```console
$ echo "more code" >> app.py && git add app.py
$ sc modify
Amended commit on 'add-application'
```

The commit message is preserved, and the Shortcake-Parent trailer remains:

```console
$ git log -1 --format=%B | grep Shortcake-Parent
Shortcake-Parent: main
```

## Creating a New Commit with -m

Use `-m` to create a new commit instead of amending:

```console
$ echo "feature code" >> app.py && git add app.py
$ sc modify -m "Add new feature"
Created commit on 'add-application'
```

The new commit also has the parent trailer:

```console
$ git log -1 --format=%B | grep Shortcake-Parent
Shortcake-Parent: main
```

## Verifying the Stack

After modifications, the stack structure is preserved:

```console
$ sc ls
◉ add-application (current)
│ Add new feature
│
◯ main
  Initial commit
```

## Modifying with Multiple Commits

Create more commits and verify the log:

```console
$ echo "helper code" >> app.py && git add app.py
$ sc modify -m "Add helper functions"
Created commit on 'add-application'
$ sc log
◉ add-application
│
● 0848a55 Add helper functions
│
● 20517b9 Add new feature
│
● fde0168 Add application
│
◯ main
```

## Error Cases

Cannot amend without staged changes:

```console
$ sc modify
Error: No staged changes to amend
```

Cannot create commit without staged changes:

```console
$ sc modify -m "Should fail"
Error: No staged changes to commit
```

Cannot use both -m and -e:

```console
$ echo "change" >> app.py && git add app.py
$ sc modify -m "message" -e
Error: Cannot use both -m and -e
$ git checkout -- app.py
```

Cannot use both -t and -m:

```console
$ echo "change" >> app.py && git add app.py
$ sc modify -t main -m "message"
Error: Cannot use both -t and -m
$ git checkout -- app.py
```

## Edge Cases

Target branch does not exist:

```console
$ echo "change" >> app.py && git add app.py
$ sc modify -t nonexistent-branch
Error: Branch 'nonexistent-branch' does not exist
$ git checkout HEAD -- app.py
```

Target branch is not tracked by Shortcake:

```console
$ git checkout -b untracked-branch
$ echo "untracked" > untracked.py && git add untracked.py && git commit -m "untracked" > /dev/null
$ git checkout add-application
$ echo "change" >> app.py && git add app.py
$ sc modify -t untracked-branch
Error: Branch 'untracked-branch' is not tracked by Shortcake
$ git checkout HEAD -- app.py
```

Cannot modify in detached HEAD state:

```console
$ git checkout --detach
$ sc modify -m "Should fail"
Error: Cannot modify in detached HEAD state
$ git checkout add-application
```
