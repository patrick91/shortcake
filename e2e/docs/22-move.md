# Moving Branches

The `sc move` command moves a tracked branch to a new parent by rebasing it and updating the Shortcake-Parent trailer.

## Setup: Create a Stack

Create a stack of three branches: a → b → c.

```console
$ echo "feature a" > feature_a.py && git add feature_a.py
$ sc create -m "Add feature A"
Created branch 'add-feature-a' from 'main'
```

```console
$ echo "feature b" > feature_b.py && git add feature_b.py
$ sc create -m "Add feature B"
Created branch 'add-feature-b' from 'add-feature-a'
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
◯ add-feature-a
│
◯ main
```

## Basic Move

Move feature C from feature B to main:

```console
$ sc move -p main
Rebasing 'add-feature-c' onto 'main'...
Moved 'add-feature-c' from 'add-feature-b' to 'main'.
```

Verify the trailer:

```console
$ git log -1 --format=%B add-feature-c | grep Shortcake-Parent
Shortcake-Parent: main
```

## Verify File Contents

The moved branch keeps its own file:

```console
$ cat feature_c.py
feature c
```

## Move With Explicit Branch

Move feature B from feature A to main:

```console
$ sc move add-feature-b -p main
Rebasing 'add-feature-b' onto 'main'...
Moved 'add-feature-b' from 'add-feature-a' to 'main'.
```

```console
$ git log -1 --format=%B add-feature-b | grep Shortcake-Parent
Shortcake-Parent: main
```

## Same Parent (No-op)

```console
$ sc move add-feature-b -p main
Branch 'add-feature-b' already has parent 'main'. Nothing to do.
```

## Error Cases

Move onto self:

```console
$ sc move add-feature-a -p add-feature-a
Error: Cannot move 'add-feature-a' onto itself
```

Parent not found:

```console
$ sc move add-feature-a -p nonexistent
Error: Parent branch 'nonexistent' not found
```

Uncommitted changes:

```console
$ echo "dirty" >> feature_c.py
$ sc move -p add-feature-a
Error: You have uncommitted changes. Commit or stash them first.
$ git checkout -- feature_c.py
```
