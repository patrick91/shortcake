# Folding Changes When Context Differs Between Branches

When using `sc modify --target`, the staged changes are captured as a patch
relative to the current branch. If an intermediate branch modified the same
file, the context lines in the patch won't match the target branch's version.
The operation should still succeed by using a three-way merge.

## Setup: Create a Stack Where an Intermediate Branch Modifies a Shared File

Create branch-a with a shared file, then branch-b that modifies it:

```console
$ printf "line 1\nline 2\nline 3\n" > shared.py && git add shared.py
$ sc create -m "Add shared file"
Created branch 'add-shared-file' from 'main'
```

```console
$ printf "line 1\nline 2\nline 3\nbranch-b addition\n" > shared.py && git add shared.py
$ sc create -m "Extend shared file"
Created branch 'extend-shared-file' from 'add-shared-file'
```

Verify the stack:

```console
$ sc ls
◉ extend-shared-file (current)
│ Extend shared file
│
◯ add-shared-file
│ Add shared file
│
◯ main
  Initial commit
```

## Fold a Change into an Ancestor When Context Differs

From branch-b, modify a line that also exists on branch-a, but the surrounding
context differs (branch-b added an extra line). Stage it and fold into branch-a:

```console
$ printf "line 1 updated\nline 2\nline 3\nbranch-b addition\n" > shared.py && git add shared.py
$ sc modify -t add-shared-file
Folded staged changes into 'add-shared-file'
Restacked 1 branch(es).
```

We should still be on branch-b:

```console
$ git branch --show-current
extend-shared-file
```

Verify the change landed in branch-a's diff:

```console
$ git diff main..add-shared-file -- shared.py
diff --git a/shared.py b/shared.py
new file mode 100644
index 0000000..7009b7e
--- /dev/null
+++ b/shared.py
@@ -0,0 +1,3 @@
+line 1 updated
+line 2
+line 3
```
