# Reordering Branches

The `sc reorder` command rearranges branches within a stack. Pass the desired order as positional arguments (bottom-to-top).

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

## Basic Reorder

Move feature C to the bottom of the stack:

```console
$ sc reorder add-feature-c add-feature-a add-feature-b
Rebasing 'add-feature-c' onto 'main'...
Rebasing 'add-feature-a' onto 'add-feature-c'...
Rebasing 'add-feature-b' onto 'add-feature-a'...
Reordered 3 branch(es) successfully.
```

Verify the new stack order:

```console
$ sc ls
◯ add-feature-b
│
◯ add-feature-a
│
◉ add-feature-c (current)
│
◯ main
```

## Verify File Contents

All files are preserved after reorder. The top branch has all files:

```console
$ sc top
Switched to 'add-feature-b'
$ cat feature_a.py
feature a
$ cat feature_b.py
feature b
$ cat feature_c.py
feature c
```

## Verify Trailers

```console
$ git log -1 --format=%B add-feature-c | grep Shortcake-Parent
Shortcake-Parent: main
```

```console
$ git log -1 --format=%B add-feature-a | grep Shortcake-Parent
Shortcake-Parent: add-feature-c
```

```console
$ git log -1 --format=%B add-feature-b | grep Shortcake-Parent
Shortcake-Parent: add-feature-a
```

## Same Order (No-op)

```console
$ sc reorder add-feature-c add-feature-a add-feature-b
Stack is already in the requested order.
```

## Error Cases

Unknown branch name:

```console
$ sc reorder add-feature-c unknown add-feature-b
Error: Invalid reorder: unknown: unknown; missing: add-feature-a. Must be a permutation of the current stack.
```

Wrong number of branches (missing one):

```console
$ sc reorder add-feature-c add-feature-a
Error: Invalid reorder: missing: add-feature-b. Must be a permutation of the current stack.
```
