# Empty Commits During Restack

When commits become empty during rebase (changes already in target),
shortcake automatically skips them while preserving other changes in the stack.

## Setup: Create a Stack

Create a stack with two branches, each adding a different file:

```console
$ echo "feature A" > file_a.txt && git add file_a.txt
$ sc create -m "Add feature A"
Created branch 'add-feature-a' from 'main'
$ echo "feature B" > file_b.txt && git add file_b.txt
$ sc create -m "Add feature B"
Created branch 'add-feature-b' from 'add-feature-a'
```

## Simulate Squash Merge of First Branch

Main receives the same changes as the first branch (simulating a squash merge):

```console
$ git checkout main
$ echo "feature A" > file_a.txt && git add file_a.txt && git commit -m "squash merge feature A"
[main 24b6c2a] squash merge feature A
 1 file changed, 1 insertion(+)
 create mode 100644 file_a.txt
```

## Restack Skips Empty, Preserves Other Changes

When restacking, the first branch's commit becomes empty but the second branch's changes are preserved:

```console
$ git checkout add-feature-b
$ sc restack
Rebasing 'add-feature-a' onto 'main'...
  Skipped empty commit (changes already in 'main')
Rebasing 'add-feature-b' onto 'add-feature-a'...
Restacked 2 branch(es) successfully.
```

## Verify Changes Preserved

The second branch still has its unique changes (file_b.txt):

```console
$ git diff main --stat
 file_b.txt | 1 +
 1 file changed, 1 insertion(+)
$ cat file_b.txt
feature B
```
