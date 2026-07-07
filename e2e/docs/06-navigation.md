# Navigating Stacks

Shortcake provides commands to move between branches in a stack.

## Setup: Create a 3-Branch Stack

```console
$ echo "base code" > base.py && git add base.py
$ sc create -m "Add base module"
Created branch 'add-base-module' from 'main'
$ echo "middle code" > middle.py && git add middle.py
$ sc create -m "Add middle layer"
Created branch 'add-middle-layer' from 'add-base-module'
$ echo "top code" > top.py && git add top.py
$ sc create -m "Add top layer"
Created branch 'add-top-layer' from 'add-middle-layer'
$ sc ls
◉ add-top-layer (current)
│ Add top layer
│
◯ add-middle-layer
│ Add middle layer
│
◯ add-base-module
│ Add base module
│
◯ main
  Initial commit
```

## Moving Down the Stack

Use `sc down` to move to the parent branch:

```console
$ sc down
Switched to 'add-middle-layer'
$ git branch --show-current
add-middle-layer
```

Move down again:

```console
$ sc down
Switched to 'add-base-module'
```

When you reach the trunk branch, it tells you:

```console
$ sc down
Switched to 'main' (bottom of stack)
```

## Moving Up the Stack

Use `sc up` to move to the child branch:

```console
$ git checkout add-base-module
$ sc up
Switched to 'add-middle-layer'
```

```console
$ sc up
Switched to 'add-top-layer'
```

When at the top of the stack:

```console
$ sc up
Already at top of stack (no children)
```

## Jumping to Top

Use `sc top` to jump directly to the leaf branch:

```console
$ git checkout main
$ git checkout add-base-module
$ sc top
Switched to 'add-top-layer'
$ git branch --show-current
add-top-layer
```

When already at top:

```console
$ sc top
Already at top of stack
```

## Jumping to Bottom

Use `sc bottom` to jump to the first branch above trunk:

```console
$ sc bottom
Switched to 'add-base-module'
$ git branch --show-current
add-base-module
```

When already at bottom:

```console
$ sc bottom
Already at bottom of stack
```

## Navigating with Forked Stacks

Create a fork where one branch has two children:

```console
$ git checkout add-base-module
$ echo "fork a" > fork_a.py && git add fork_a.py
$ sc create -m "Add fork A"
Created branch 'add-fork-a' from 'add-base-module'
$ git checkout add-base-module
$ echo "fork b" > fork_b.py && git add fork_b.py
$ sc create -m "Add fork B"
Created branch 'add-fork-b' from 'add-base-module'
```

When at a branch with one child, `sc up` moves to it directly:

```console
$ git checkout main
$ sc up
Switched to 'add-base-module'
```

You can specify which child to navigate to:

```console
$ sc up add-fork-a
Switched to 'add-fork-a'
```

## Error Cases

Cannot navigate down from an untracked branch:

```console
$ git checkout main
$ sc down
Error: Branch 'main' is not tracked
```

Cannot navigate up from a leaf:

```console
$ git checkout add-fork-a
$ sc up
Already at top of stack (no children)
```

Cannot jump to bottom from an untracked branch:

```console
$ git checkout main
$ sc bottom
Error: Branch 'main' is not tracked
```

## Up with Invalid Child Argument

Running `sc up <branch>` with a branch that is not a child of the current branch:

```console
$ git checkout add-base-module
$ sc up main
Error: 'main' is not a child of 'add-base-module'
```

## Detached HEAD Errors

Navigation commands fail from detached HEAD state:

```console
$ git checkout --detach
$ sc up
Error: Not on a branch (detached HEAD)
$ sc down
Error: Not on a branch (detached HEAD)
$ sc top
Error: Not on a branch (detached HEAD)
$ sc bottom
Error: Not on a branch (detached HEAD)
```
