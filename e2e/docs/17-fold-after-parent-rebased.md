# Folding After Parent Branch Was Rebased

When a parent branch is rebased or modified (e.g., via `sc modify` or after
`sc restack`), `git merge-base` between the child and parent may point to an
ancestor that's too old. This would cause `sc fold` to include the parent's
changes in the diff, leading to "patch does not apply" or
"No such file or directory" errors.

The fold command avoids this by using the git parent of the child's first
commit (the one with the Shortcake-Parent trailer) as the diff base, which is
stable regardless of whether the parent was rebased.

## Setup: Create a Stack

Create branch-a with a file, then branch-b that adds its own file:

```console
$ echo "base from a" > a.py && git add a.py
$ sc create -m "Add a.py"
Created branch 'add-a-py' from 'main'
```

```console
$ echo "from b" > b.py && git add b.py
$ sc create -m "Add b.py"
Created branch 'add-b-py' from 'add-a-py'
```

Verify the stack:

```console
$ sc ls
◉ add-b-py (current)
│ Add b.py
│
◯ add-a-py
│ Add a.py
│
◯ main
  Initial commit
```

## Modify the Parent Branch

Use `sc modify` to change a.py from branch-b. This rebases add-a-py
so its HEAD SHA changes, making the git merge-base stale for add-b-py:

```console
$ echo "base from a modified" > a.py && git add a.py
$ sc modify -t add-a-py
Folded staged changes into 'add-a-py'
Restacked 1 branch(es).
```

Verify add-a-py has the updated content:

```console
$ cat a.py
base from a modified
```

## Fold Child Into Parent Despite Stale Merge Base

Now fold add-b-py into its parent. This should succeed even though
the parent was rebased:

```console
$ sc fold
Folded 'add-b-py' into 'add-a-py'
```

We're now on add-a-py:

```console
$ git branch --show-current
add-a-py
```

The folded branch is deleted:

```console
$ git branch --list add-b-py
```

Branch b's file is now in add-a-py:

```console
$ cat b.py
from b
```

The stack is clean:

```console
$ sc ls
◉ add-a-py (current)
│ Add a.py
│
◯ main
  Initial commit
```
