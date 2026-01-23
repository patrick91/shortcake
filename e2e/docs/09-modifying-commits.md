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
│
◯ main
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
● c54ff86 Add helper functions
│
● e7aa221 Add new feature
│
● ef69aac Add application
│
◯ main
```
