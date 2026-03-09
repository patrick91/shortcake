# Checkout

The `sc checkout` command provides smart branch switching.

## Setup

Create some branches to checkout:

```console
$ echo "feature a" > a.txt && git add a.txt
$ sc create -m "Add feature A"
Created branch 'add-feature-a' from 'main'
$ sc down
Switched to 'main' (bottom of stack)
```

## Basic Checkout

Switch to an existing branch:

```console
$ sc checkout add-feature-a
Switched to 'add-feature-a'
```

## Using the co Alias

The `co` command is an alias for `checkout`:

```console
$ sc down
Switched to 'main' (bottom of stack)
$ sc co add-feature-a
Switched to 'add-feature-a'
```

## Checkout by PR Number

You can checkout a branch by its PR number:

```console
$ # github: setup-mock-with-remote
$ git checkout main > /dev/null 2>&1
$ echo "pr feature" > pr.py && git add pr.py
$ sc create -m "PR feature"
Created branch 'pr-feature' from 'main'
$ git push -u origin pr-feature > /dev/null 2>&1
$ # github: add-pr pr-feature 42 main
$ git checkout main > /dev/null 2>&1
$ sc co 42
Checked out PR #42 (pr-feature)
```

## Checkout Remote Branch

When a branch exists on remote but not locally, checkout fetches it:

```console
$ # reset-to-main
$ # setup: with-remote
$ echo "remote feature" > remote.py && git add remote.py
$ sc create -m "Remote feature"
Created branch 'remote-feature' from 'main'
$ git push -u origin remote-feature > /dev/null 2>&1
$ git checkout main > /dev/null 2>&1
$ git branch -D remote-feature > /dev/null 2>&1
$ sc co remote-feature
Checked out 'remote-feature' from remote
```

## Error Cases

Branch that doesn't exist:

```console
$ sc co nonexistent-branch
Error: Branch 'nonexistent-branch' not found on remote.
```
