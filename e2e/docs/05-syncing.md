# Syncing with Remote

The `--sync` flag fetches from remote before restacking. It can detect diverged branches.

## Setup: Create a Stack with Remote

```console
$ # setup: with-remote
$ echo "feature code" > feature.py && git add feature.py
$ sc create -m "Add feature"
Created branch 'add-feature' from 'main'
$ git push -u origin add-feature
branch 'add-feature' set up to track 'origin/add-feature'.
```

## Basic Sync

When everything is up to date, sync reports nothing to do:

```console
$ sc restack --sync
Fetching from origin...
Everything up to date.
```

## Divergence Detection

When a branch has diverged from remote (both have unique commits), restack warns:

```console
$ echo "local change" >> feature.py && git add feature.py && git commit -m "Local change"
[add-feature 5ce08a0] Local change
 1 file changed, 1 insertion(+)
$ # remote: force-push add-feature
$ git fetch origin
$ sc restack --sync
Fetching from origin...
Warning: Branches diverged from remote: add-feature
Run 'git pull --rebase' on each diverged branch first.
Or use 'sc restack --sync' to auto-fetch and fast-forward.
Error: Cannot restack with diverged branches
```

## Fixing Divergence

Rebase onto the remote branch to incorporate the remote changes:

```console
$ git rebase origin/add-feature
$ sc restack --sync
Fetching from origin...
Everything up to date.
```
