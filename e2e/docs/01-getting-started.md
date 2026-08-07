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
│ Add user authentication
│
◯ main
  Initial commit
```

The `*` indicates your current branch.

## Checking Git Status

After creating a branch, your working directory should be clean:

```console
$ git status
On branch add-user-authentication
nothing to commit, working tree clean
```

## Creating an Empty Branch

Use `--allow-empty` to create a branch without staged changes:

```console
$ sc create -m "Empty placeholder" --allow-empty
Created branch 'empty-placeholder' from 'add-user-authentication'
```

## Detached HEAD

When detached at the tip of one local branch, `sc create` uses that branch as
the parent:

```console
$ git checkout --detach
$ sc create -m "Detached work" --allow-empty
Created branch 'detached-work' from 'empty-placeholder'
$ git log -1 --format=%B | grep Shortcake-Parent
Shortcake-Parent: empty-placeholder
```

If no branch points at `HEAD`, Shortcake uses the default branch and includes
the detached commits in the new branch. Use `--parent` to choose another base or
when more than one local branch points at the detached commit.

## Error Cases

Cannot create without staged changes:

```console
$ sc create -m "Should fail"
Error: No staged changes. Use --allow-empty to create anyway.
```
