# Getting Started with Shortcake

Shortcake helps you manage stacked PRs using git trailers.

## Creating Your First Branch

First, stage some changes:

```console
$ echo "# Authentication Module" > auth.py
$ git add auth.py
```

Then create a feature branch with `sc create`:

```console
$ sc create -m "Add user authentication"
Created branch 'add-user-authentication' from 'main'
```

This creates a new branch and adds a `Shortcake-Parent` trailer to track the parent branch.

Check your branch was created:

```console
$ git branch --show-current
add-user-authentication
```

Verify the commit has the parent trailer:

```console
$ git log -1 --format=%B | grep Shortcake-Parent
Shortcake-Parent: main
```

## Viewing Your Stack

Use `sc ls` to see all tracked branches:

```console
$ sc ls
◉ add-user-authentication (current)
│
◯ main
```

The `*` indicates your current branch.

## Checking Git Status

After creating a branch, your working directory should be clean:

```console
$ git status
On branch add-user-authentication
nothing to commit, working tree clean
```
