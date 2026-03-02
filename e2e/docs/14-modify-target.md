# Folding Changes into Another Branch

The `sc modify --target` command lets you fold staged changes into another branch's commit without switching branches manually.

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

## Fold Staged Changes into Another Branch

From branch c, modify a file that belongs to branch b, stage it, and fold into b:

```console
$ echo "feature b updated" > feature_b.py && git add feature_b.py
$ sc modify -t add-feature-b
Folded staged changes into 'add-feature-b'
Restacked 1 branch(es).
```

We're still on branch c:

```console
$ git branch --show-current
add-feature-c
```

Verify the change landed in branch b's diff:

```console
$ git diff add-base-app..add-feature-b -- feature_b.py
diff --git a/feature_b.py b/feature_b.py
new file mode 100644
index 0000000..08cec9b
--- /dev/null
+++ b/feature_b.py
@@ -0,0 +1 @@
+feature b updated
```

## Incompatible Options

The `--target` flag cannot be combined with `-m` or `-e`:

```console
$ sc modify -t add-feature-b -m "message"
Error: Cannot use both -t and -m
```

```console
$ sc modify -t add-feature-b -e
Error: Cannot use both -t and -e
```
