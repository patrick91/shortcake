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
│
◯ add-middle-layer
│
◯ add-base-module
│
◯ main
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
