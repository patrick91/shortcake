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

## Auto-Rebase on Divergence

When a branch has diverged from remote (both have unique commits), restack auto-rebases:

```console
$ echo "local change" >> feature.py && git add feature.py && git commit -m "Local change"
[add-feature 956eb06] Local change
 1 file changed, 1 insertion(+)
$ # remote: force-push add-feature
$ git fetch origin
$ sc restack --sync
Fetching from origin...
Rebasing 'add-feature' onto 'origin/add-feature'...
Everything up to date.
```

## Fixing Divergence

Rebase onto the remote branch to incorporate the remote changes:

```console
$ git rebase origin/add-feature
Current branch add-feature is up to date.
$ sc restack --sync
Fetching from origin...
Everything up to date.
```
