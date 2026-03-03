# Inserting Branches

The `sc create` command supports `--before` and `--after` flags to insert a new branch into an existing stack without having to manually reorder.

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

## Insert Before

Switch to feature B and insert a new branch before it:

```console
$ sc checkout add-feature-b
Switched to 'add-feature-b'
```

```console
$ sc create -m "Add fix before B" --before --allow-empty
Rebasing 'add-feature-b' onto 'add-fix-before-b'...
Created branch 'add-fix-before-b' from 'add-feature-a'
Rebased 'add-feature-b' onto 'add-fix-before-b'
```

Verify the new stack order:

```console
$ sc ls
◯ add-feature-c
│
◯ add-feature-b
│
◉ add-fix-before-b (current)
│
◯ add-feature-a
│
◯ main
```

Verify trailers are correct:

```console
$ git log -1 --format=%B add-fix-before-b | grep Shortcake-Parent
Shortcake-Parent: add-feature-a
```

```console
$ git log -1 --format=%B add-feature-b | grep Shortcake-Parent
Shortcake-Parent: add-fix-before-b
```

## Insert After

Switch to feature A and insert a new branch after it:

```console
$ sc checkout add-feature-a
Switched to 'add-feature-a'
```

```console
$ sc create -m "Add fix after A" --after --allow-empty
Rebasing 'add-fix-before-b' onto 'add-fix-after-a'...
Created branch 'add-fix-after-a' from 'add-feature-a'
Rebased 'add-fix-before-b' onto 'add-fix-after-a'
```

Verify the new stack order:

```console
$ sc ls
◯ add-feature-c
│
◯ add-feature-b
│
◯ add-fix-before-b
│
◉ add-fix-after-a (current)
│
◯ add-feature-a
│
◯ main
```

## Insert After Leaf (No Rebase)

Switch to the top of the stack and insert after it (no children to rebase):

```console
$ sc top
Switched to 'add-feature-c'
```

```console
$ sc create -m "Add feature D" --after --allow-empty
Created branch 'add-feature-d' from 'add-feature-c'
```

```console
$ sc ls
◉ add-feature-d (current)
│
◯ add-feature-c
│
◯ add-feature-b
│
◯ add-fix-before-b
│
◯ add-fix-after-a
│
◯ add-feature-a
│
◯ main
```

## Error Cases

Cannot use both `--before` and `--after`:

```console
$ sc create -m "fail" --before --after --allow-empty
Error: Cannot use both --before and --after.
```

Cannot insert before an untracked branch:

```console
$ sc checkout main
Switched to 'main'
$ sc create -m "fail" --before --allow-empty
Error: Branch 'main' is not tracked by Shortcake. Cannot insert before an untracked branch.
```
