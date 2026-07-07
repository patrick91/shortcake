# Adopting Existing Branches

The `sc adopt` command lets you track an existing branch that wasn't created with Shortcake.

## Setup: Create an Untracked Branch

Create a branch using regular git (not `sc create`):

```console
$ git checkout -b feature-login
$ echo "login code" > login.py && git add login.py
$ git commit -m "Add login functionality"
[feature-login 41e5ba4] Add login functionality
 1 file changed, 1 insertion(+)
 create mode 100644 login.py
```

This branch has no `Shortcake-Parent` trailer:

```console
$ git log -1 --format=%B | grep Shortcake-Parent || echo "No trailer found"
No trailer found
```

## Adopting the Branch

Use `sc adopt` to add tracking:

```console
$ sc adopt
Adopted 'feature-login' with parent 'main'
```

Now the branch has the parent trailer:

```console
$ git log -1 --format=%B | grep Shortcake-Parent
Shortcake-Parent: main
```

The branch appears in `sc ls`:

```console
$ sc ls
◉ feature-login (current)
│ Add login functionality
│
◯ main
  Initial commit
```

## Adopting with a Specific Parent

You can specify a different parent branch:

```console
$ git checkout main
$ git checkout -b feature-api
$ echo "api code" > api.py && git add api.py
$ git commit -m "Add API layer"
[feature-api 2117e10] Add API layer
 1 file changed, 1 insertion(+)
 create mode 100644 api.py
$ sc adopt --parent feature-login
Adopted 'feature-api' with parent 'feature-login'
$ sc ls
◉ feature-api (current) ⟳ needs restack
│ Add API layer
│
◯ feature-login
│ Add login functionality
│
◯ main
  Initial commit
```

## Adopting a Different Branch

You can adopt a branch without being on it:

```console
$ git checkout main
$ git checkout -b feature-utils
$ echo "utils" > utils.py && git add utils.py
$ git commit -m "Add utilities"
[feature-utils ff78693] Add utilities
 1 file changed, 1 insertion(+)
 create mode 100644 utils.py
$ git checkout main
$ sc adopt feature-utils
Adopted 'feature-utils' with parent 'main'
```

## Re-parenting with --force

When a branch is already tracked, `--force` lets you change its parent:

```console
$ git checkout feature-login
$ sc adopt --force --parent feature-utils
Re-parented 'feature-login' to 'feature-utils'
$ git log -1 --format=%B | grep Shortcake-Parent
Shortcake-Parent: feature-utils
$ sc ls
◯ feature-api ⟳ needs restack
│ Add API layer
│
◉ feature-login (current) ⟳ needs restack
│ Add login functionality
│
◯ feature-utils
│ Add utilities
│
◯ main
  Initial commit
```

## Error Cases

Cannot adopt the default branch:

```console
$ git checkout main
$ sc adopt
Error: Cannot adopt default branch 'main'
```

Parent branch doesn't exist:

```console
$ git checkout feature-login
$ sc adopt --parent nonexistent
Error: Parent branch 'nonexistent' not found
```

Already tracked without --force:

```console
$ sc adopt
Error: Branch 'feature-login' is already tracked by 'feature-utils'. Use --force to re-parent.
```
