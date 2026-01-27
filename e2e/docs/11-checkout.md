# Checkout

The `sc checkout` command provides smart branch switching with automatic adoption of untracked branches.

## Setup

Create some branches to checkout:

```console
$ echo "feature a" > a.txt && git add a.txt
$ sc create -m "Add feature A"
Created branch 'add-feature-a' from 'main'
$ sc down
Switched to branch 'main'
```

## Basic Checkout

Switch to an existing untracked branch and it gets adopted automatically:

```console
$ git branch untracked-feature
$ echo "untracked" > untracked.txt && git add untracked.txt
$ git checkout untracked-feature && git commit -m "Add untracked feature"
[untracked-feature ...] Add untracked feature
 1 file changed, 1 insertion(+)
 create mode 100644 untracked.txt
$ git checkout main
$ sc checkout untracked-feature
Switched to 'untracked-feature'
  Adopted 'untracked-feature' for stack tracking
```

## Checkout with --no-adopt

Skip automatic adoption with the `--no-adopt` flag:

```console
$ git checkout main
$ git branch another-untracked
$ echo "another" > another.txt && git add another.txt
$ git checkout another-untracked && git commit -m "Add another"
[another-untracked ...] Add another
 1 file changed, 1 insertion(+)
 create mode 100644 another.txt
$ git checkout main
$ sc checkout --no-adopt another-untracked
Switched to 'another-untracked'
```

No adoption message is shown.

## Already Tracked Branches

Branches that are already tracked don't get re-adopted:

```console
$ git checkout main
$ sc checkout add-feature-a
Switched to 'add-feature-a'
```

No adoption message for already tracked branches.

## Using the co Alias

The `co` command is an alias for `checkout`:

```console
$ sc down
Switched to branch 'main'
$ sc co add-feature-a
Switched to 'add-feature-a'
```
