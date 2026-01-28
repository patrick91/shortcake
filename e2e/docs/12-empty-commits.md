# Empty Commits During Restack

When commits become empty during rebase (changes already in target),
shortcake automatically skips them and continues with the restack.

## Setup

Create a feature branch from main:

```console
$ echo "base content" > file.txt && git add file.txt
$ sc create -m "Add feature"
Created branch 'add-feature' from 'main'
```

## Duplicate Change in Main

Simulate a squash merge that brings the same changes to main:

```console
$ git checkout main
$ echo "base content" > file.txt && git add file.txt && git commit -m "squash merge"
[main a6d96fc] squash merge
 1 file changed, 1 insertion(+)
 create mode 100644 file.txt
```

## Restack Skips Empty

When restacking, the commit becomes empty because main already has the changes:

```console
$ git checkout add-feature
$ sc restack
Rebasing 'add-feature' onto 'main'...
  Skipped empty commit (changes already in 'main')
Restacked 1 branch(es) successfully.
```

The branch is now up to date with main, and the empty commit was automatically skipped.
