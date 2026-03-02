# Folding Branches

The `sc fold` command absorbs the current branch into another branch (by default its parent), removing it from the stack and re-parenting any children.

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

## Basic Fold into Parent

Fold the middle branch (feature B) into its parent (base app):

```console
$ sc checkout add-feature-b
Switched to 'add-feature-b'
$ sc fold
Folded 'add-feature-b' into 'add-base-app'
Re-parented 'add-feature-c' to 'add-base-app'
Restacked 1 branch(es).
```

We're now on the target branch (add-base-app):

```console
$ git branch --show-current
add-base-app
```

The folded branch is deleted:

```console
$ git branch --list add-feature-b
```

Feature B's changes are in add-base-app:

```console
$ cat feature_b.py
feature b
```

Feature C is re-parented to add-base-app:

```console
$ sc ls
◯ add-feature-c
│
◉ add-base-app (current)
│
◯ main
```

## Setup: Create Another Stack for --into

```console
$ sc checkout add-feature-c
Switched to 'add-feature-c'
```

```console
$ echo "feature d" > feature_d.py && git add feature_d.py
$ sc create -m "Add feature D"
Created branch 'add-feature-d' from 'add-feature-c'
```

```console
$ sc ls
◉ add-feature-d (current)
│
◯ add-feature-c
│
◯ add-base-app
│
◯ main
```

## Fold with --into

Fold feature D into add-base-app (non-adjacent):

```console
$ sc fold --into add-base-app
Folded 'add-feature-d' into 'add-base-app'
Restacked 1 branch(es).
```

```console
$ git branch --show-current
add-base-app
```

Feature D's file is in add-base-app:

```console
$ cat feature_d.py
feature d
```

```console
$ sc ls
◯ add-feature-c
│
◉ add-base-app (current)
│
◯ main
```

## Error Cases

Cannot fold an untracked branch:

```console
$ git checkout -b untracked-branch > /dev/null 2>&1
$ sc fold
Error: Branch 'untracked-branch' is not tracked by Shortcake
```

Cannot fold into nonexistent branch:

```console
$ sc checkout add-feature-c
Switched to 'add-feature-c'
$ sc fold --into nonexistent
Error: Branch 'nonexistent' does not exist
```

Cannot fold into self:

```console
$ sc fold --into add-feature-c
Error: Cannot fold a branch into itself
```
